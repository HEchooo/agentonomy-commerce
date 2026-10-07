"""Bounded Monad testnet contract deployment operator.

This module is deliberately separate from the purchase signer.  It accepts
only a locally regenerated two-transaction creation plan, keeps every signed
transaction in a private journal, and uses two read-only RPC observations
before and after the single permitted broadcast endpoint.  The default path
only validates local input and never imports the AWS signer.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import fcntl
import inspect
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from typing import Any, Callable, Iterator, Mapping

from eth_abi import decode, encode
from eth_account import Account
from eth_account._utils.legacy_transactions import Transaction
from eth_utils import keccak
import rlp

from agentonomy_commerce.kms_adapter import sign_legacy_transaction
from scripts.monad.deployment_plan import (
    BUYER,
    CHAIN_ID,
    DEPLOYER,
    EXECUTION_SIGNER,
    MAX_GAS_PRICE_WEI,
    RPC_URLS,
    SUPPLY,
    TOKEN_DECIMALS,
    ROOT,
    build_plan,
    _canonical_hash as _planner_canonical_hash,
)


JOURNAL_SCHEMA = "monad-deployment-journal-v1"
_HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_HEX_RE = re.compile(r"0x[0-9a-fA-F]*\Z")
_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_JOURNAL_KEYS = {"schema_version", "plan_sha256", "transactions", "status", "summary"}
_RECORD_KEYS = {
    "index",
    "transaction",
    "raw_transaction",
    "tx_hash",
    "status",
    "attempted",
    "summary",
}
_SUMMARY_KEYS = {
    "plan_sha256",
    "status",
    "funding_status",
    "pending",
    "finalized_boundary",
    "transactions",
}
_TX_SUMMARY_KEYS = {
    "index",
    "status",
    "tx_hash",
    "address",
    "block_number",
    "code_hash",
    "finalized_boundary",
}

# Kept patchable for deterministic tests.  Importing this module does not
# create an HTTP client or load any AWS code.
RpcClient: Any = None

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class OperatorError(ValueError):
    """A safe, non-secret operator failure."""


class PendingEvidence(OperatorError):
    """Evidence is temporarily unavailable; keep the attempted hash pending."""


class TransientRpcError(PendingEvidence):
    """A provider/network read failed without proving a transaction invalid."""


def _address(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _ADDRESS_RE.fullmatch(value) is None:
        raise OperatorError(f"{name} is invalid")
    if int(value, 16) == 0:
        raise OperatorError(f"{name} is invalid")
    return value.lower()


def _hash(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise OperatorError(f"{name} is invalid")
    if int(value, 16) == 0:
        raise OperatorError(f"{name} is invalid")
    return value.lower()


def _hex_bytes(value: object, *, name: str, allow_empty: bool = True) -> bytes:
    if not isinstance(value, str) or _HEX_RE.fullmatch(value) is None:
        raise OperatorError(f"{name} is invalid")
    if len(value[2:]) % 2:
        raise OperatorError(f"{name} is invalid")
    try:
        result = bytes.fromhex(value[2:])
    except ValueError:
        raise OperatorError(f"{name} is invalid") from None
    if not allow_empty and not result:
        raise OperatorError(f"{name} is empty")
    return result


def _quantity(value: object, *, name: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)\Z", value) is None:
        raise OperatorError(f"{name} is invalid")
    return int(value, 16)


def _canonical_plan_hash(plan: Mapping[str, object]) -> str:
    # Keep this implementation intentionally identical to deployment_plan's
    # canonical hash.  Calling the existing helper also protects against
    # accidental drift in a future planner revision.
    return _planner_canonical_hash(dict(plan))


def validate_plan(plan: object, *, root: Path = ROOT) -> dict[str, object]:
    """Regenerate and strictly compare a concrete deployment plan.

    Only the planner's two runtime fields, ``status`` and ``rpc_check``, are
    ignored during the deterministic comparison.  Every other field,
    including artifact hashes, constructor data and RPC pins, must match the
    current local artifacts exactly.
    """

    if not isinstance(plan, dict):
        raise OperatorError("plan must be an object")
    if "plan_sha256" not in plan:
        raise OperatorError("plan hash is required")
    if not isinstance(plan["plan_sha256"], str) or re.fullmatch(r"[0-9a-f]{64}\Z", plan["plan_sha256"]) is None:
        raise OperatorError("plan hash is invalid")
    if "status" in plan and not isinstance(plan["status"], str):
        raise OperatorError("plan status is invalid")
    if "rpc_check" in plan and not isinstance(plan["rpc_check"], dict):
        raise OperatorError("plan RPC check is invalid")
    for field in ("nonce", "gas_price_wei", "gas_limit"):
        if type(plan.get(field)) is not int:
            raise OperatorError("plan numeric field is invalid")
    if plan.get("payee") != DEPLOYER.lower():
        raise OperatorError("plan payee is outside the fixed deployment scope")
    if plan.get("rpc_urls") != list(RPC_URLS):
        raise OperatorError("plan RPC scope is outside the fixed deployment scope")
    try:
        expected = build_plan(
            nonce=plan["nonce"],  # type: ignore[arg-type]
            gas_price_wei=plan["gas_price_wei"],  # type: ignore[arg-type]
            payee=DEPLOYER,
            gas_limit=plan["gas_limit"],  # type: ignore[arg-type]
            root=Path(root),
        )
    except (OSError, TypeError, ValueError):
        raise OperatorError("local deployment artifacts or plan inputs are invalid") from None

    extra = set(plan) - set(expected) - {"status", "rpc_check"}
    missing = set(expected) - set(plan)
    if extra or missing:
        raise OperatorError("plan fields do not match the regenerated local plan")

    actual = copy.deepcopy(plan)
    expected_without_runtime = copy.deepcopy(expected)
    for value in (actual, expected_without_runtime):
        value.pop("status", None)
        value.pop("rpc_check", None)
    if actual != expected_without_runtime:
        raise OperatorError("plan does not match the regenerated local plan")
    if plan["plan_sha256"] != expected["plan_sha256"]:
        raise OperatorError("plan hash does not match the regenerated local plan")
    if _canonical_plan_hash(plan) != plan["plan_sha256"]:
        raise OperatorError("plan hash is not canonical")
    return plan


def load_plan(path: Path, *, root: Path = ROOT) -> dict[str, object]:
    path = Path(path)
    try:
        if path.is_symlink():
            raise OperatorError("plan symlink is not accepted")
        plan = json.loads(path.read_text(encoding="utf-8"))
    except OperatorError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise OperatorError("plan file is invalid") from None
    return validate_plan(plan, root=root)


def _rpc_factory_default() -> Callable[..., object]:
    global RpcClient
    if RpcClient is None:
        from agentonomy_commerce.budget_network import RpcClient as _RpcClient

        RpcClient = _RpcClient
    return RpcClient


def _make_rpc(factory: Callable[..., object], url: str, *, writable: bool = False) -> object:
    try:
        parameters = inspect.signature(factory).parameters
        accepts_writable = "writable" in parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        )
    except (TypeError, ValueError):
        accepts_writable = True
    try:
        if accepts_writable:
            return factory(url, writable=writable)
        return factory(url)
    except TypeError:
        # A tiny fake factory commonly accepts only the URL.  Do not expose
        # constructor details in an operator error.
        try:
            return factory(url)
        except Exception:
            raise OperatorError("RPC client unavailable") from None
    except Exception:
        raise OperatorError("RPC client unavailable") from None


def _rpc_call(client: object, method: str, params: list[object]) -> object:
    try:
        return client.call(method, params)  # type: ignore[attr-defined]
    except Exception:
        raise TransientRpcError("RPC request failed") from None


def _preflight(plan: Mapping[str, object], index: int, clients: list[object]) -> dict[str, object]:
    transactions = plan["transactions"]
    assert isinstance(transactions, list)
    transaction = transactions[index]
    assert isinstance(transaction, dict)
    target_map = plan["predicted_addresses"]
    assert isinstance(target_map, dict)
    target = target_map["token" if index == 0 else "executor"]
    assert isinstance(target, str)
    expected_nonce = transaction["nonce"]
    expected_gas_price = transaction["gasPrice"]
    expected_gas_limit = transaction["gas"]
    if not all(type(value) is int for value in (expected_nonce, expected_gas_price, expected_gas_limit)):
        raise OperatorError("plan transaction numbers are invalid")

    observations: list[dict[str, object]] = []
    for client in clients:
        chain_id = _quantity(_rpc_call(client, "eth_chainId", []), name="chain ID")
        pending = _quantity(
            _rpc_call(client, "eth_getTransactionCount", [DEPLOYER.lower(), "pending"]),
            name="pending nonce",
        )
        latest = _quantity(
            _rpc_call(client, "eth_getTransactionCount", [DEPLOYER.lower(), "latest"]),
            name="latest nonce",
        )
        provider_gas_price = _quantity(_rpc_call(client, "eth_gasPrice", []), name="gas price")
        balance = _quantity(
            _rpc_call(client, "eth_getBalance", [DEPLOYER.lower(), "latest"]),
            name="gas balance",
        )
        code = _hex_bytes(
            _rpc_call(client, "eth_getCode", [target.lower(), "latest"]),
            name="target code",
        )
        estimate_payload = {
            "from": DEPLOYER.lower(),
            "to": None,
            "value": "0x0",
            "data": transaction["data"],
            "gasPrice": hex(expected_gas_price),
        }
        estimate = _quantity(
            _rpc_call(client, "eth_estimateGas", [estimate_payload]),
            name="gas estimate",
        )
        observations.append(
            {
                "chain_id": chain_id,
                "pending_nonce": pending,
                "latest_nonce": latest,
                "gas_price_wei": provider_gas_price,
                "balance_wei": balance,
                "target_code": code.hex(),
                "estimate_gas": estimate,
            }
        )

    if len(observations) != 2 or observations[0] != observations[1]:
        raise OperatorError("RPC preflight observations disagree")
    observation = observations[0]
    if observation["chain_id"] != CHAIN_ID:
        raise OperatorError("RPC chain ID mismatch")
    if observation["pending_nonce"] != expected_nonce or observation["latest_nonce"] != expected_nonce:
        raise OperatorError("RPC nonce does not match the planned transaction")
    if observation["gas_price_wei"] <= 0 or observation["gas_price_wei"] > expected_gas_price:
        raise OperatorError("RPC gas price exceeds the concrete plan")
    remaining = (2 - index) * expected_gas_price * expected_gas_limit
    if observation["balance_wei"] < remaining:
        raise OperatorError("insufficient gas balance for the bounded plan")
    if observation["target_code"] != "":
        raise OperatorError("predicted deployment target already has code")
    if observation["estimate_gas"] <= 0 or observation["estimate_gas"] > expected_gas_limit:
        raise OperatorError("gas estimate exceeds the transaction cap")
    return {
        "funding_status": "sufficient",
        "gas_estimate": observation["estimate_gas"],
        "balance_wei": observation["balance_wei"],
        "target": target.lower(),
    }


def _private_path_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            if current.is_symlink():
                raise OperatorError("journal path contains a symlink")
        except OSError:
            raise OperatorError("journal path is unavailable") from None


def _prepare_journal_parent(path: Path) -> Path:
    path = Path(path)
    parent = path.parent if str(path.parent) else Path(".")
    _private_path_components(parent)
    try:
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = os.lstat(parent)
    except OSError:
        raise OperatorError("journal directory is unavailable") from None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise OperatorError("journal parent must be private")
    if path.exists() or path.is_symlink():
        try:
            metadata = os.lstat(path)
        except OSError:
            raise OperatorError("journal file is unavailable") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise OperatorError("journal file symlink is not accepted")
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise OperatorError("journal file must be private")
    return parent


@contextmanager
def _journal_lock(path: Path) -> Iterator[None]:
    parent = _prepare_journal_parent(path)
    lock_path = Path(str(path) + ".lock")
    _private_path_components(lock_path)
    try:
        lock_metadata = os.lstat(lock_path)
    except FileNotFoundError:
        lock_exists = False
    except OSError:
        raise OperatorError("journal lock is unavailable") from None
    else:
        lock_exists = True
        # Validate an operator-supplied lock path before opening or changing
        # it.  In particular, do not chmod an existing special file or
        # directory, and never open a FIFO in blocking mode.
        if stat.S_ISLNK(lock_metadata.st_mode):
            raise OperatorError("journal lock symlink is not accepted")
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or stat.S_IMODE(lock_metadata.st_mode) != 0o600
            or lock_metadata.st_nlink != 1
        ):
            raise OperatorError("journal lock must be private")
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(str(path.absolute()), threading.Lock())
    if not thread_lock.acquire(blocking=False):
        raise OperatorError("journal is already locked")
    descriptor: int | None = None
    try:
        flags = os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        if not lock_exists:
            flags |= os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise OperatorError("journal lock must be private")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                raise OperatorError("journal is already locked") from None
            yield
        except OperatorError:
            raise
        except OSError:
            raise OperatorError("journal lock is unavailable") from None
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(descriptor)
            except OSError:
                pass
        thread_lock.release()


def _write_atomic(path: Path, value: Mapping[str, object]) -> None:
    parent = _prepare_journal_parent(path)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        directory_descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError:
        raise OperatorError("journal write failed") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _read_journal(path: Path, plan: Mapping[str, object]) -> dict[str, object] | None:
    if not path.exists() and not path.is_symlink():
        return None
    _prepare_journal_parent(path)
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise OperatorError("journal file must be private")
        if metadata.st_size > 4_000_000:
            raise OperatorError("journal file is too large")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = None
            value = json.load(stream)
    except OperatorError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise OperatorError("journal file is invalid") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    _validate_journal(value, plan)
    return value


def _new_journal(plan: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": JOURNAL_SCHEMA,
        "plan_sha256": plan["plan_sha256"],
        "transactions": [],
        "status": "prepared",
        "summary": {
            "plan_sha256": plan["plan_sha256"],
            "status": "prepared",
            "funding_status": "unknown",
            "pending": False,
            "finalized_boundary": None,
            "transactions": [],
        },
    }


def _expected_signing_transaction(transaction: Mapping[str, object]) -> dict[str, object]:
    return {
        "chainId": transaction["chainId"],
        "nonce": transaction["nonce"],
        "to": None,
        "value": transaction["value"],
        "data": transaction["data"],
        "gasPrice": transaction["gasPrice"],
        "gas": transaction["gas"],
        "from": DEPLOYER.lower(),
    }


def _verify_saved_raw(record: Mapping[str, object]) -> None:
    raw = _hex_bytes(record.get("raw_transaction"), name="journal raw transaction", allow_empty=False)
    tx_hash = _hash(record.get("tx_hash"), name="journal transaction hash")
    if "transaction" not in record or not isinstance(record["transaction"], dict):
        raise OperatorError("journal transaction is invalid")
    transaction = record["transaction"]
    try:
        decoded = rlp.decode(raw, Transaction)
        recovered = Account.recover_transaction(raw)
    except Exception:
        raise OperatorError("journal signed transaction is invalid") from None
    if _address(recovered, name="journal signer") != DEPLOYER.lower():
        raise OperatorError("journal signed transaction signer mismatch")
    if type(decoded.v) is not int or decoded.v < 35 or (decoded.v - 35) // 2 != CHAIN_ID:
        raise OperatorError("journal signed transaction chain mismatch")
    if (decoded.v - 35) % 2 not in (0, 1):
        raise OperatorError("journal signed transaction signature is invalid")
    expected_data = _hex_bytes(transaction.get("data"), name="journal transaction data")
    if (
        decoded.nonce != transaction.get("nonce")
        or decoded.gasPrice != transaction.get("gasPrice")
        or decoded.gas != transaction.get("gas")
        or decoded.value != transaction.get("value")
        or decoded.to not in (b"", None)
        or decoded.data != expected_data
        or transaction.get("chainId") != CHAIN_ID
    ):
        raise OperatorError("journal signed transaction differs from the original transaction")
    if "from" in transaction and _address(transaction["from"], name="journal transaction sender") != DEPLOYER.lower():
        raise OperatorError("journal transaction sender mismatch")
    if "0x" + keccak(raw).hex() != tx_hash:
        raise OperatorError("journal transaction hash mismatch")


def _validate_journal(value: object, plan: Mapping[str, object]) -> None:
    if not isinstance(value, dict) or set(value) != _JOURNAL_KEYS:
        raise OperatorError("journal shape is invalid")
    if value["schema_version"] != JOURNAL_SCHEMA or value["plan_sha256"] != plan["plan_sha256"]:
        raise OperatorError("journal does not match the concrete plan")
    if not isinstance(value["status"], str) or value["status"] not in {"prepared", "pending", "complete", "failed"}:
        raise OperatorError("journal status is invalid")
    summary = value["summary"]
    if not isinstance(summary, dict) or set(summary) != _SUMMARY_KEYS:
        raise OperatorError("journal summary is invalid")
    if summary["plan_sha256"] != plan["plan_sha256"] or summary["status"] != value["status"]:
        raise OperatorError("journal summary does not match journal state")
    if not isinstance(summary["funding_status"], str) or summary["funding_status"] not in {"unknown", "sufficient"}:
        raise OperatorError("journal funding status is invalid")
    if type(summary["pending"]) is not bool:
        raise OperatorError("journal pending status is invalid")
    if summary["finalized_boundary"] is not None and type(summary["finalized_boundary"]) is not int:
        raise OperatorError("journal finality boundary is invalid")
    if type(summary["finalized_boundary"]) is int and summary["finalized_boundary"] < 0:
        raise OperatorError("journal finality boundary is invalid")
    if not isinstance(summary["transactions"], list):
        raise OperatorError("journal transaction summary is invalid")
    transactions = plan["transactions"]
    if not isinstance(transactions, list) or not isinstance(value["transactions"], list):
        raise OperatorError("journal transactions are invalid")
    if len(value["transactions"]) not in {1, 2}:
        raise OperatorError("journal transaction count is invalid")
    for index, record in enumerate(value["transactions"]):
        if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
            raise OperatorError("journal transaction record is invalid")
        if type(record["index"]) is not int or record["index"] != index or record["transaction"] != transactions[index]:
            raise OperatorError("journal transaction changed after signing")
        if not isinstance(record["status"], str) or record["status"] not in {"prepared", "pending", "verified", "failed"}:
            raise OperatorError("journal transaction status is invalid")
        if type(record["attempted"]) is not bool:
            raise OperatorError("journal attempt state is invalid")
        if record["status"] == "prepared" and record["attempted"]:
            raise OperatorError("prepared journal transaction cannot be attempted")
        if record["status"] != "prepared" and not record["attempted"]:
            raise OperatorError("journal transaction attempt state is invalid")
        record_summary = record["summary"]
        if not isinstance(record_summary, dict) or set(record_summary) != _TX_SUMMARY_KEYS:
            raise OperatorError("journal public transaction summary is invalid")
        if type(record_summary["index"]) is not int or record_summary["index"] != index or record_summary["status"] != record["status"]:
            raise OperatorError("journal public transaction summary does not match state")
        if record_summary["tx_hash"] != record["tx_hash"]:
            raise OperatorError("journal public transaction hash does not match state")
        _hash(record_summary["tx_hash"], name="journal public transaction hash")
        address = record_summary["address"]
        block_number = record_summary["block_number"]
        code_hash = record_summary["code_hash"]
        finalized_boundary = record_summary["finalized_boundary"]
        if address is not None:
            _address(address, name="journal public contract address")
        if block_number is not None and (type(block_number) is not int or block_number < 0):
            raise OperatorError("journal public block number is invalid")
        if code_hash is not None:
            _hash(code_hash, name="journal public code hash")
        if finalized_boundary is not None and (type(finalized_boundary) is not int or finalized_boundary < 0):
            raise OperatorError("journal public finality boundary is invalid")
        if record["status"] == "verified":
            if address is None or block_number is None or code_hash is None or finalized_boundary is None:
                raise OperatorError("verified journal transaction evidence is incomplete")
            if finalized_boundary < block_number:
                raise OperatorError("verified journal finality precedes its transaction")
        elif any(item is not None for item in (address, block_number, code_hash, finalized_boundary)):
            raise OperatorError("unverified journal transaction contains deployment evidence")
        _verify_saved_raw(record)
    if value["status"] == "complete":
        if len(value["transactions"]) != 2 or any(
            not isinstance(record, dict) or record["status"] != "verified" or record["attempted"] is not True
            for record in value["transactions"]
        ):
            raise OperatorError("complete journal must contain two verified transactions")
        if summary["pending"] is not False or summary["finalized_boundary"] is None:
            raise OperatorError("complete journal finality summary is invalid")
        if summary["funding_status"] != "sufficient":
            raise OperatorError("complete journal funding summary is invalid")
    if len(summary["transactions"]) != len(value["transactions"]):
        raise OperatorError("journal summary transaction count is invalid")
    for index, item in enumerate(summary["transactions"]):
        if not isinstance(item, dict) or set(item) != _TX_SUMMARY_KEYS or item != value["transactions"][index]["summary"]:
            raise OperatorError("journal summary transaction mismatch")


def _record_summary(index: int, *, status: str, tx_hash: str, address: str | None = None,
                    block_number: int | None = None, code_hash: str | None = None,
                    finalized_boundary: int | None = None) -> dict[str, object]:
    return {
        "index": index,
        "status": status,
        "tx_hash": tx_hash,
        "address": address,
        "block_number": block_number,
        "code_hash": code_hash,
        "finalized_boundary": finalized_boundary,
    }


def _new_record(index: int, transaction: dict[str, object], raw: bytes) -> dict[str, object]:
    tx_hash = "0x" + keccak(raw).hex()
    record = {
        "index": index,
        "transaction": copy.deepcopy(transaction),
        "raw_transaction": "0x" + raw.hex(),
        "tx_hash": tx_hash,
        "status": "prepared",
        "attempted": False,
        "summary": _record_summary(index, status="prepared", tx_hash=tx_hash),
    }
    _verify_saved_raw(record)
    return record


def _save_journal(path: Path, journal: dict[str, object], plan: Mapping[str, object]) -> None:
    _validate_journal(journal, plan)
    _write_atomic(path, journal)


def _abi_selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def _call_abi(client: object, to: str, signature: str, types: list[str], values: list[object], block: str) -> object:
    data = "0x" + (_abi_selector(signature) + (encode(types, values) if types else b"")).hex()
    result = _rpc_call(client, "eth_call", [{"to": to.lower(), "data": data}, block])
    return _hex_bytes(result, name="contract read", allow_empty=False)


def _decode_abi_value(raw: bytes, typ: str, *, name: str) -> object:
    try:
        return decode([typ], raw)[0]
    except Exception:
        raise OperatorError(f"{name} is malformed") from None


def _read_token_identity(
    client: object,
    plan: Mapping[str, object],
    boundary: str,
    *,
    include_buyer_balance: bool = True,
) -> dict[str, object]:
    addresses = plan["predicted_addresses"]
    assert isinstance(addresses, dict)
    token = addresses["token"]
    assert isinstance(token, str)
    symbol = _decode_abi_value(bytes(_call_abi(client, token, "symbol()", [], [], boundary)), "string", name="token symbol")
    decimals = _decode_abi_value(bytes(_call_abi(client, token, "decimals()", [], [], boundary)), "uint8", name="token decimals")
    total_supply = _decode_abi_value(bytes(_call_abi(client, token, "totalSupply()", [], [], boundary)), "uint256", name="token supply")
    buyer_balance = None
    if include_buyer_balance:
        buyer_balance = _decode_abi_value(
            bytes(_call_abi(client, token, "balanceOf(address)", ["address"], [BUYER], boundary)),
            "uint256",
            name="buyer balance",
        )
    if symbol != "TestUSD" or decimals != TOKEN_DECIMALS or total_supply != SUPPLY or (
        include_buyer_balance and buyer_balance != SUPPLY
    ):
        raise OperatorError("deployed token identity does not match the bounded plan")
    identity = {
        "symbol": symbol,
        "decimals": decimals,
        "total_supply": total_supply,
    }
    if include_buyer_balance:
        identity["buyer_balance"] = buyer_balance
    return identity


def _expected_domain_separator(executor: str) -> str:
    type_hash = keccak(text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
    encoded = encode(
        ["bytes32", "bytes32", "bytes32", "uint256", "address"],
        [type_hash, keccak(text="Agentonomy Budget Executor"), keccak(text="1"), CHAIN_ID, executor],
    )
    return "0x" + keccak(encoded).hex()


def _read_executor_identity(client: object, plan: Mapping[str, object], boundary: str) -> dict[str, object]:
    addresses = plan["predicted_addresses"]
    assert isinstance(addresses, dict)
    token = addresses["token"]
    executor = addresses["executor"]
    assert isinstance(token, str) and isinstance(executor, str)
    token_value = _decode_abi_value(bytes(_call_abi(client, executor, "TOKEN()", [], [], boundary)), "address", name="executor token")
    chain_value = _decode_abi_value(
        bytes(_call_abi(client, executor, "EXECUTION_CHAIN_ID()", [], [], boundary)),
        "uint256",
        name="executor chain",
    )
    domain_raw = bytes(_call_abi(client, executor, "DOMAIN_SEPARATOR()", [], [], boundary))
    domain_value = "0x" + domain_raw.hex()
    if str(token_value).lower() != token.lower() or chain_value != CHAIN_ID or domain_value != _expected_domain_separator(executor):
        raise OperatorError("deployed executor identity does not match the bounded plan")
    return {
        "token": token.lower(),
        "execution_chain_id": chain_value,
        "domain_separator": domain_value,
    }


def _validate_rpc_transaction(transaction: object, receipt: object, expected: Mapping[str, object], tx_hash: str) -> tuple[dict[str, object], int, str]:
    if not isinstance(transaction, dict) or not isinstance(receipt, dict):
        raise OperatorError("deployment RPC evidence is malformed")
    observed_hash = _hash(transaction.get("hash"), name="transaction hash")
    receipt_hash = _hash(receipt.get("transactionHash"), name="receipt transaction hash")
    observed_from = _address(transaction.get("from"), name="transaction sender")
    observed_to = transaction.get("to")
    if observed_to is not None:
        raise OperatorError("deployment transaction target is not CREATE")
    input_value = transaction.get("input")
    data_value = transaction.get("data")
    if input_value is None and data_value is None:
        raise OperatorError("deployment transaction input is missing")
    input_bytes = _hex_bytes(input_value if input_value is not None else data_value, name="transaction input")
    if input_value is not None and data_value is not None and _hex_bytes(input_value, name="transaction input") != _hex_bytes(data_value, name="transaction data"):
        raise OperatorError("deployment transaction input fields disagree")
    tx_chain = _quantity(transaction.get("chainId"), name="transaction chain")
    nonce = _quantity(transaction.get("nonce"), name="transaction nonce")
    value = _quantity(transaction.get("value"), name="transaction value")
    gas = _quantity(transaction.get("gas"), name="transaction gas")
    gas_price = _quantity(transaction.get("gasPrice"), name="transaction gas price")
    block_number = _quantity(transaction.get("blockNumber"), name="transaction block")
    block_hash = _hash(transaction.get("blockHash"), name="transaction block hash")
    receipt_block_number = _quantity(receipt.get("blockNumber"), name="receipt block")
    receipt_block_hash = _hash(receipt.get("blockHash"), name="receipt block hash")
    status = _quantity(receipt.get("status"), name="receipt status")
    contract = _address(receipt.get("contractAddress"), name="created contract")
    expected_data = _hex_bytes(expected.get("data"), name="planned transaction data")
    if (
        observed_hash != tx_hash
        or receipt_hash != tx_hash
        or observed_from != DEPLOYER.lower()
        or tx_chain != CHAIN_ID
        or nonce != expected.get("nonce")
        or value != 0
        or input_bytes != expected_data
        or gas != expected.get("gas")
        or gas_price != expected.get("gasPrice")
        or block_number != receipt_block_number
        or block_hash != receipt_block_hash
    ):
        raise OperatorError("deployment transaction evidence differs from the saved transaction")
    if status not in {0, 1}:
        raise OperatorError("deployment receipt status is invalid")
    if status == 0:
        raise OperatorError("deployment transaction reverted")
    return {
        "tx_hash": tx_hash,
        "block_number": block_number,
        "block_hash": block_hash,
        "contract_address": contract,
        "status": status,
    }, block_number, block_hash


def _reconcile_record(plan: Mapping[str, object], record: Mapping[str, object], clients: list[object]) -> dict[str, object]:
    index = record["index"]
    assert isinstance(index, int)
    tx_hash = _hash(record["tx_hash"], name="journal transaction hash")
    transactions = plan["transactions"]
    addresses = plan["predicted_addresses"]
    assert isinstance(transactions, list) and isinstance(addresses, dict)
    expected = transactions[index]
    assert isinstance(expected, dict)
    target = addresses["token" if index == 0 else "executor"]
    assert isinstance(target, str)
    observations: list[dict[str, object]] = []
    missing: list[bool] = []
    for client in clients:
        chain_id = _quantity(_rpc_call(client, "eth_chainId", []), name="chain ID")
        tx = _rpc_call(client, "eth_getTransactionByHash", [tx_hash])
        receipt = _rpc_call(client, "eth_getTransactionReceipt", [tx_hash])
        if tx is None or receipt is None:
            missing.append(True)
            observations.append({"chain_id": chain_id, "tx": tx, "receipt": receipt})
            continue
        proof, block_number, block_hash = _validate_rpc_transaction(tx, receipt, expected, tx_hash)
        canonical = _rpc_call(client, "eth_getBlockByNumber", [hex(block_number), False])
        finalized = _rpc_call(client, "eth_getBlockByNumber", ["finalized", False])
        if canonical is None or finalized is None or not isinstance(canonical, dict) or not isinstance(finalized, dict):
            missing.append(True)
            observations.append({"chain_id": chain_id, "tx": tx, "receipt": receipt, "pending": True})
            continue
        canonical_number = _quantity(canonical.get("number"), name="canonical block number")
        canonical_hash = _hash(canonical.get("hash"), name="canonical block hash")
        finalized_number = _quantity(finalized.get("number"), name="finalized block number")
        finalized_hash = _hash(finalized.get("hash"), name="finalized block hash")
        if canonical_number != block_number or canonical_hash != block_hash:
            raise OperatorError("deployment receipt block is not canonical")
        if finalized_number < block_number + 3:
            missing.append(True)
            observations.append({"chain_id": chain_id, "tx": tx, "receipt": receipt, "pending": True})
            continue
        missing.append(False)
        observations.append(
            {
                "chain_id": chain_id,
                "proof": proof,
                "finalized_number": finalized_number,
                "finalized_hash": finalized_hash,
            }
        )
    if all(missing):
        return {
            "status": "pending",
            "summary": _record_summary(index, status="pending", tx_hash=tx_hash),
        }
    if any(missing):
        raise PendingEvidence("RPC deployment evidence is temporarily incomplete")
    if len(observations) != 2 or observations[0]["chain_id"] != CHAIN_ID or observations[1]["chain_id"] != CHAIN_ID:
        raise PendingEvidence("RPC chain observations disagree temporarily")
    proof_a = observations[0]["proof"]
    proof_b = observations[1]["proof"]
    if proof_a != proof_b:
        raise PendingEvidence("RPC deployment transaction evidence disagrees temporarily")
    finality_a = observations[0]["finalized_number"]
    finality_b = observations[1]["finalized_number"]
    assert isinstance(finality_a, int) and isinstance(finality_b, int)
    boundary_number = min(finality_a, finality_b) - 3
    saved_boundary = record.get("summary", {}).get("finalized_boundary")
    if type(saved_boundary) is int:
        if saved_boundary > boundary_number or saved_boundary < block_number:
            raise OperatorError("saved verified boundary is no longer available")
        # Replays use the originally verified historical boundary.  This keeps
        # the constructor's initial buyer balance meaningful after later token
        # transfers while still checking that current finality covers it.
        boundary_number = saved_boundary
    boundary_tag = hex(boundary_number)
    boundary_blocks = []
    code_values = []
    for client in clients:
        boundary_block = _rpc_call(client, "eth_getBlockByNumber", [boundary_tag, False])
        if not isinstance(boundary_block, dict):
            raise OperatorError("verified boundary is unavailable")
        boundary_blocks.append(
            {
                "number": _quantity(boundary_block.get("number"), name="boundary number"),
                "hash": _hash(boundary_block.get("hash"), name="boundary hash"),
            }
        )
        code_values.append(_hex_bytes(_rpc_call(client, "eth_getCode", [target.lower(), boundary_tag]), name="deployed code", allow_empty=False))
    if boundary_blocks[0] != boundary_blocks[1] or boundary_blocks[0]["number"] != boundary_number:
        raise PendingEvidence("RPC verified boundary observations disagree temporarily")
    if code_values[0] != code_values[1]:
        raise PendingEvidence("RPC deployed code observations disagree temporarily")
    expected_address = target.lower()
    if proof_a["contract_address"] != expected_address:
        raise OperatorError("created contract address differs from the deterministic plan")
    identity_values = []
    for client in clients:
        token_identity_tag = hex(proof_a["block_number"]) if index == 0 else boundary_tag
        identity = {
            "token": _read_token_identity(
                client,
                plan,
                token_identity_tag,
                include_buyer_balance=index == 0,
            )
        }
        if index == 1:
            identity["executor"] = _read_executor_identity(client, plan, boundary_tag)
        identity_values.append(identity)
    if identity_values[0] != identity_values[1]:
        raise PendingEvidence("RPC deployed contract identity observations disagree temporarily")
    code_hash = "0x" + keccak(code_values[0]).hex()
    return {
        "status": "verified",
        "summary": _record_summary(
            index,
            status="verified",
            tx_hash=tx_hash,
            address=expected_address,
            block_number=proof_a["block_number"],
            code_hash=code_hash,
            finalized_boundary=boundary_number,
        ),
    }


def _public_summary(plan: Mapping[str, object], journal: Mapping[str, object], *, status: str,
                    funding_status: str | None = None, pending: bool | None = None,
                    finalized_boundary: int | None = None, reason: str | None = None) -> dict[str, object]:
    summary = journal.get("summary")
    existing = summary if isinstance(summary, dict) else {}
    result: dict[str, object] = {
        "plan_sha256": plan["plan_sha256"],
        "status": status,
        "funding_status": funding_status if funding_status is not None else existing.get("funding_status", "unknown"),
        "pending": pending if pending is not None else existing.get("pending", status == "pending"),
        "finalized_boundary": finalized_boundary if finalized_boundary is not None else existing.get("finalized_boundary"),
        "transactions": copy.deepcopy(existing.get("transactions", [])),
    }
    if reason is not None:
        result["reason"] = reason
    return result


def _update_summary(journal: dict[str, object], *, status: str, funding_status: str | None = None,
                    pending: bool | None = None, finalized_boundary: int | None = None) -> None:
    summary = journal["summary"]
    assert isinstance(summary, dict)
    summary["status"] = status
    if funding_status is not None:
        summary["funding_status"] = funding_status
    if pending is not None:
        summary["pending"] = pending
    if finalized_boundary is not None:
        summary["finalized_boundary"] = finalized_boundary
    summary["transactions"] = [record["summary"] for record in journal["transactions"]]  # type: ignore[index]
    journal["status"] = status


def _get_gas_signer(signer: object) -> object:
    gas_signer = getattr(signer, "gas_signer", signer)
    if not callable(getattr(gas_signer, "sign_digest", None)):
        raise OperatorError("gas signer capability is unavailable")
    return gas_signer


def _sign_record(transaction: Mapping[str, object], signer: object) -> bytes:
    gas_signer = _get_gas_signer(signer)
    signing_transaction = _expected_signing_transaction(transaction)
    try:
        return bytes(
            sign_legacy_transaction(
                signing_transaction,
                gas_signer.sign_digest,  # type: ignore[attr-defined]
                expected_address=DEPLOYER,
                chain_id=CHAIN_ID,
            )
        )
    except Exception:
        raise OperatorError("transaction signing failed") from None


def _broadcast_and_reconcile(
    path: Path,
    journal: dict[str, object],
    plan: Mapping[str, object],
    record: dict[str, object],
    clients: list[object],
    factory: Callable[..., object],
) -> dict[str, object]:
    # The attempted marker is durable before the network write.  A timeout or
    # malformed provider response therefore leaves the exact raw bytes in the
    # journal and forces later invocations into reconciliation only.
    record["attempted"] = True
    record["status"] = "pending"
    record["summary"] = _record_summary(record["index"], status="pending", tx_hash=record["tx_hash"])  # type: ignore[arg-type]
    _update_summary(journal, status="pending", pending=True)
    _save_journal(path, journal, plan)
    tx_hash = record["tx_hash"]
    raw_transaction = record["raw_transaction"]
    try:
        writable = _make_rpc(factory, RPC_URLS[0], writable=True)
        result = _rpc_call(writable, "eth_sendRawTransaction", [raw_transaction])
        if _hash(result, name="broadcast transaction hash") != tx_hash:
            raise OperatorError("broadcast response did not match the saved transaction")
    except OperatorError:
        # A transport exception or nonmatching provider response leaves the
        # write outcome unknown.  Do not read either provider in this call:
        # the next invocation reconciles the exact saved hash and may then
        # advance to the second CREATE after canonical proof is available.
        _update_summary(journal, status="pending", pending=True)
        _save_journal(path, journal, plan)
        return _public_summary(plan, journal, status="pending", pending=True)

    # A matching response permits same-call evidence reconciliation.  Any
    # incomplete evidence remains pending and is retried against the original
    # hash by a later invocation.
    try:
        evidence = _reconcile_record(plan, record, clients)
    except PendingEvidence:
        _update_summary(journal, status="pending", pending=True)
        _save_journal(path, journal, plan)
        return _public_summary(plan, journal, status="pending", pending=True)
    except OperatorError:
        _update_summary(journal, status="failed", pending=False)
        record["status"] = "failed"
        record["summary"] = _record_summary(record["index"], status="failed", tx_hash=tx_hash)  # type: ignore[arg-type]
        _update_summary(journal, status="failed", pending=False)
        _save_journal(path, journal, plan)
        return _public_summary(
            plan,
            journal,
            status="blocked",
            pending=False,
            reason="deployment evidence is invalid",
        )
    record["status"] = evidence["status"]
    record["summary"] = evidence["summary"]
    journal_status = "pending" if evidence["status"] == "verified" else evidence["status"]
    _update_summary(journal, status=journal_status, pending=evidence["status"] == "pending")
    _save_journal(path, journal, plan)
    return _public_summary(plan, journal, status=evidence["status"], pending=evidence["status"] == "pending")


def _aws_signer_factory(path: Path, plan: Mapping[str, object]) -> Callable[[], object]:
    """Create the CLI-only lazy AWS factory after validating public scope."""

    try:
        if path.is_symlink():
            raise OperatorError("signer configuration symlink is not accepted")
        configuration = json.loads(path.read_text(encoding="utf-8"))
        from agentonomy_commerce.signer_process import _load_signer, validate_configuration

        scope = validate_configuration(configuration)
    except OperatorError:
        raise
    except Exception:
        raise OperatorError("signer configuration is invalid") from None
    try:
        network = scope.network
        addresses = plan["predicted_addresses"]
        assert isinstance(addresses, dict)
        if (
            not isinstance(configuration.get("expected_role_arn"), str)
            or not configuration.get("expected_role_arn")
            or scope.network.mode != "monad_testnet"
            or scope.network.chain_id != CHAIN_ID
            or tuple(network.rpc_urls) != tuple(RPC_URLS)
            or scope.network.token != addresses["token"].lower()
            or scope.network.executor != addresses["executor"].lower()
            or scope.network.payee != DEPLOYER.lower()
            or scope.owner != BUYER.lower()
            or scope.execution_address != EXECUTION_SIGNER.lower()
            or scope.relayer_address != DEPLOYER.lower()
        ):
            raise OperatorError("signer configuration is outside the deployment scope")
    except (AttributeError, KeyError, TypeError):
        raise OperatorError("signer configuration is outside the deployment scope") from None

    def factory() -> object:
        with _sanitized_aws_environment(configuration):
            try:
                return _load_signer(configuration, scope)
            except Exception:
                raise OperatorError("AWS signer unavailable") from None

    return factory


@contextmanager
def _sanitized_aws_environment(configuration: Mapping[str, object]) -> Iterator[None]:
    saved = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("AWS_") or key in {"HOME"}
    }
    for key in list(saved):
        os.environ.pop(key, None)
    credential_directory = Path(str(configuration["credential_directory"]))
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    os.environ["AWS_SHARED_CREDENTIALS_FILE"] = str(credential_directory / "credentials")
    os.environ["AWS_CONFIG_FILE"] = str(credential_directory / "config")
    os.environ["HOME"] = str(credential_directory)
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith("AWS_") or key == "HOME":
                os.environ.pop(key, None)
        os.environ.update(saved)


def run_deployment(
    plan: Mapping[str, object],
    journal_path: Path,
    *,
    execute: bool = False,
    signer_factory: Callable[[], object] | None = None,
    rpc_factory: Callable[..., object] | None = None,
    root: Path = ROOT,
) -> dict[str, object]:
    """Validate, execute, or reconcile a concrete deployment plan.

    ``signer_factory`` is intentionally lazy.  It is never called for status
    checks, failed preflight, existing attempted records, or completed replay.
    """

    validated = validate_plan(dict(plan), root=root)
    path = Path(journal_path)
    with _journal_lock(path):
        journal = _read_journal(path, validated) or _new_journal(validated)
        transactions = validated["transactions"]
        assert isinstance(transactions, list)
        factory = rpc_factory or _rpc_factory_default()
        signer_sentinel = object()
        cached_signer: object = signer_sentinel

        def get_signer() -> object:
            nonlocal cached_signer
            if cached_signer is signer_sentinel:
                if signer_factory is None:
                    raise OperatorError("signer is required")
                try:
                    cached_signer = signer_factory()
                except OperatorError:
                    raise
                except Exception:
                    raise OperatorError("signer unavailable") from None
            return cached_signer

        # A completed journal is evidence to recheck, not an authority to
        # trust.  The same dual-RPC evidence path is used on every replay.
        if journal["transactions"] and journal["status"] == "complete":
            clients = [_make_rpc(factory, url) for url in RPC_URLS]
            boundaries: list[int] = []
            for record in journal["transactions"]:
                assert isinstance(record, dict)
                try:
                    evidence = _reconcile_record(validated, record, clients)
                except PendingEvidence:
                    _update_summary(journal, status="pending", pending=True)
                    _save_journal(path, journal, validated)
                    return _public_summary(validated, journal, status="pending", pending=True)
                except OperatorError:
                    record["status"] = "failed"
                    record["summary"] = _record_summary(record["index"], status="failed", tx_hash=record["tx_hash"])  # type: ignore[arg-type]
                    _update_summary(journal, status="failed", pending=False)
                    _save_journal(path, journal, validated)
                    return _public_summary(validated, journal, status="blocked", pending=False, reason="deployment evidence is invalid")
                if evidence["status"] != "verified":
                    record["status"] = evidence["status"]
                    record["summary"] = evidence["summary"]
                    _update_summary(journal, status="pending", pending=True)
                    _save_journal(path, journal, validated)
                    return _public_summary(validated, journal, status="pending", pending=True)
                record["status"] = "verified"
                record["summary"] = evidence["summary"]
                boundaries.append(evidence["summary"]["finalized_boundary"])
            _update_summary(journal, status="complete", pending=False, finalized_boundary=min(boundaries))
            _save_journal(path, journal, validated)
            return _public_summary(validated, journal, status="complete", pending=False, finalized_boundary=min(boundaries))

        if not journal["transactions"]:
            if not execute:
                return {"plan_sha256": validated["plan_sha256"], "status": "validated", "pending": False}
            try:
                clients = [_make_rpc(factory, url) for url in RPC_URLS]
                _preflight(validated, 0, clients)
            except OperatorError as error:
                return {"plan_sha256": validated["plan_sha256"], "status": "blocked", "reason": str(error), "pending": False}
            if signer_factory is None:
                return {"plan_sha256": validated["plan_sha256"], "status": "blocked", "reason": "signer is required", "pending": False}
            try:
                signer = get_signer()
                raw = _sign_record(transactions[0], signer)  # type: ignore[arg-type]
                record = _new_record(0, transactions[0], raw)  # type: ignore[arg-type]
            except OperatorError as error:
                return {"plan_sha256": validated["plan_sha256"], "status": "blocked", "reason": str(error), "pending": False}
            journal["transactions"].append(record)  # type: ignore[union-attr]
            _update_summary(journal, status="prepared", funding_status="sufficient", pending=False)
            _save_journal(path, journal, validated)
        else:
            clients = [_make_rpc(factory, url) for url in RPC_URLS]

        records = journal["transactions"]
        assert isinstance(records, list)
        first = records[0]
        assert isinstance(first, dict)
        if first["status"] == "failed":
            return _public_summary(validated, journal, status="blocked", pending=False, reason="journal contains a failed transaction")
        if first["status"] == "prepared" and not first["attempted"]:
            if not execute:
                return _public_summary(validated, journal, status="prepared", pending=False)
            result = _broadcast_and_reconcile(path, journal, validated, first, clients, factory)
            if result["status"] != "verified":
                return result

        # Every attempted first transaction is rechecked before any second
        # transaction progress, including journals with [verified, pending].
        # A transient gap must preserve an existing historical proof rather
        # than downgrading that record to an evidence-free pending summary.
        if first["attempted"]:
            had_verified_evidence = first["status"] == "verified"
            try:
                evidence = _reconcile_record(validated, first, clients)
            except PendingEvidence:
                _update_summary(journal, status="pending", pending=True)
                _save_journal(path, journal, validated)
                return _public_summary(validated, journal, status="pending", pending=True)
            except OperatorError:
                first["status"] = "failed"
                first["summary"] = _record_summary(0, status="failed", tx_hash=first["tx_hash"])
                _update_summary(journal, status="failed", pending=False)
                _save_journal(path, journal, validated)
                return _public_summary(validated, journal, status="blocked", pending=False, reason="deployment evidence is invalid")
            if evidence["status"] != "verified":
                if not had_verified_evidence:
                    first["status"] = evidence["status"]
                    first["summary"] = evidence["summary"]
                _update_summary(journal, status="pending", pending=True)
                _save_journal(path, journal, validated)
                return _public_summary(validated, journal, status="pending", pending=True)
            first["status"] = "verified"
            first["summary"] = evidence["summary"]
            _update_summary(journal, status="pending", pending=False)
            _save_journal(path, journal, validated)
        if first["status"] != "verified":
            return _public_summary(validated, journal, status="blocked", pending=False, reason="first deployment transaction is not verified")

        if len(records) == 1:
            if not execute:
                return _public_summary(validated, journal, status="pending", pending=True)
            try:
                _preflight(validated, 1, clients)
            except OperatorError as error:
                _update_summary(journal, status="pending", pending=True)
                _save_journal(path, journal, validated)
                return _public_summary(validated, journal, status="blocked", pending=False, reason=str(error))
            if signer_factory is None:
                return _public_summary(validated, journal, status="blocked", pending=False, reason="signer is required")
            try:
                signer = get_signer()
                raw = _sign_record(transactions[1], signer)  # type: ignore[arg-type]
                second = _new_record(1, transactions[1], raw)  # type: ignore[arg-type]
            except OperatorError as error:
                return _public_summary(validated, journal, status="blocked", pending=False, reason=str(error))
            records.append(second)
            _update_summary(journal, status="prepared", funding_status="sufficient", pending=False)
            _save_journal(path, journal, validated)

        second = records[1]
        assert isinstance(second, dict)
        if second["status"] == "failed":
            return _public_summary(validated, journal, status="blocked", pending=False, reason="journal contains a failed transaction")
        if second["status"] == "prepared" and not second["attempted"]:
            if not execute:
                return _public_summary(validated, journal, status="prepared", pending=False)
            result = _broadcast_and_reconcile(path, journal, validated, second, clients, factory)
            if result["status"] != "verified":
                return result
        try:
            evidence = _reconcile_record(validated, second, clients)
        except PendingEvidence:
            _update_summary(journal, status="pending", pending=True)
            _save_journal(path, journal, validated)
            return _public_summary(validated, journal, status="pending", pending=True)
        except OperatorError:
            second["status"] = "failed"
            second["summary"] = _record_summary(1, status="failed", tx_hash=second["tx_hash"])
            _update_summary(journal, status="failed", pending=False)
            _save_journal(path, journal, validated)
            return _public_summary(validated, journal, status="blocked", pending=False, reason="deployment evidence is invalid")
        second["status"] = evidence["status"]
        second["summary"] = evidence["summary"]
        if evidence["status"] != "verified":
            _update_summary(journal, status="pending", pending=True)
            _save_journal(path, journal, validated)
            return _public_summary(validated, journal, status="pending", pending=True)
        boundaries = [record["summary"]["finalized_boundary"] for record in records]
        _update_summary(journal, status="complete", pending=False, finalized_boundary=min(boundaries))
        _save_journal(path, journal, validated)
        return _public_summary(validated, journal, status="complete", pending=False, finalized_boundary=min(boundaries))


def _uncertain_cli_result(plan: Mapping[str, object] | None, journal_path: Path) -> dict[str, object]:
    """Report an execution exception without asserting that no write happened."""

    result: dict[str, object] = {
        "status": "unknown",
        "broadcast": "unknown",
        "pending": True,
        "reason": "deployment outcome requires reconciliation",
    }
    if plan is None:
        return result
    try:
        journal = _read_journal(Path(journal_path), plan)
    except Exception:
        # A failed journal read cannot safely disclose a hash or claim that no
        # network write occurred.  Keep the conservative unknown result.
        return result
    if journal is None:
        return result
    records = journal.get("transactions")
    if not isinstance(records, list) or not any(
        isinstance(record, dict) and record.get("attempted") is True for record in records
    ):
        return result
    public = _public_summary(plan, journal, status="pending", pending=True)
    public["status"] = "unknown"
    public["broadcast"] = "unknown"
    public["reason"] = "deployment outcome requires reconciliation"
    return public


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--signer-config", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    plan: dict[str, object] | None = None
    try:
        plan = load_plan(args.plan, root=ROOT)
        if args.plan_sha256 != plan["plan_sha256"]:
            raise OperatorError("plan hash does not match the supplied plan")
        signer_factory = None
        if args.execute:
            if args.signer_config is None:
                raise OperatorError("--signer-config is required with --execute")
            signer_factory = _aws_signer_factory(args.signer_config, plan)
        result = run_deployment(
            plan,
            args.journal,
            execute=args.execute,
            signer_factory=signer_factory,
            root=ROOT,
        )
    except (OSError, OperatorError, TypeError, ValueError):
        if args.execute:
            result = _uncertain_cli_result(plan, args.journal)
        else:
            result = {
                "status": "blocked",
                "broadcast": False,
                "pending": False,
                "reason": "deployment operator rejected the request",
            }
    print(json.dumps(result, sort_keys=True))
    return 2 if result.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
