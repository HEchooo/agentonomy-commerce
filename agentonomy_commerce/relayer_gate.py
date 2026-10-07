"""Durable single-relayer admission for the Monad payment path.

The gate serializes the whole payment/recovery critical section for one
relayer.  It deliberately has no RPC, nonce, signing, or transaction APIs.
An active pair is retained until a trusted caller presents a verified payment
proof for that exact tenant, purchase, and owner.
"""

from __future__ import annotations

from collections.abc import Mapping
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from agentonomy_commerce.budget_network import NetworkConfig, address


_SCHEMA_VERSION = "agentonomy-relayer-gate-v1"
_JOURNAL_NAME = "relayer-gate.json"
_LOCK_NAME = "relayer-gate.lock"
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_MONAD_CHAIN_ID = 10143
_MAX_PAYMENT_ATOMIC = 500_000
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_TX_HASH_RE = re.compile(r"^0x[0-9a-f]{64}$")
_POSITIVE_UINT_RE = re.compile(r"^[1-9][0-9]*$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class RelayerGateError(ValueError):
    """The gate cannot safely admit or complete a payment."""


class RelayerGateBusy(RelayerGateError):
    """Another process currently owns the relayer gate lock."""


def _reject_json_constant(value: str) -> Any:
    raise RelayerGateError(f"journal contains invalid JSON constant {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RelayerGateError(f"journal contains duplicate key {key!r}")
        result[key] = value
    return result


def _safe_identifier(value: object, *, label: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise RelayerGateError(f"{label} must be a path-safe opaque identifier")
    return value


def _canonical_owner(value: object, *, label: str = "owner") -> str:
    if type(value) is not str:
        raise RelayerGateError(f"{label} must be an EVM address")
    try:
        return address(value)
    except ValueError as exc:
        raise RelayerGateError(f"{label} must be an EVM address") from exc


def _canonical_tx_hash(value: object) -> str:
    if type(value) is not str or _TX_HASH_RE.fullmatch(value) is None:
        raise RelayerGateError("transaction_hash must be a canonical 32-byte hash")
    return value


def _canonical_amount(value: object) -> str:
    if type(value) is not str or _POSITIVE_UINT_RE.fullmatch(value) is None:
        raise RelayerGateError("amount_atomic must be a canonical positive integer")
    amount = int(value)
    if amount > _MAX_PAYMENT_ATOMIC:
        raise RelayerGateError("amount_atomic exceeds the per-payment cap")
    return value


def _validate_payment_proof(
    proof: Mapping[str, object], network_record: Mapping[str, object]
) -> dict[str, str]:
    if not isinstance(proof, Mapping):
        raise RelayerGateError("payment proof must be an object")
    expected_keys = {
        "tenant_id",
        "purchase_id",
        "owner",
        "verified",
        "two_rpc_verified",
        "chain_id",
        "receipt_status",
        "transaction_hash",
        "token",
        "payee",
        "amount_atomic",
    }
    if set(proof) != expected_keys:
        raise RelayerGateError("payment proof schema is not exact")
    if proof["verified"] is not True or proof["two_rpc_verified"] is not True:
        raise RelayerGateError("payment proof is not independently verified")
    if type(proof["chain_id"]) is not int or proof["chain_id"] != _MONAD_CHAIN_ID:
        raise RelayerGateError("payment proof chain_id must be 10143")
    if type(proof["receipt_status"]) is not int or proof["receipt_status"] != 1:
        raise RelayerGateError("payment receipt is not successful")
    tenant_id = _safe_identifier(proof["tenant_id"], label="proof tenant_id")
    purchase_id = _safe_identifier(proof["purchase_id"], label="proof purchase_id")
    owner = _canonical_owner(proof["owner"], label="proof owner")
    token = _canonical_owner(proof["token"], label="proof token")
    payee = _canonical_owner(proof["payee"], label="proof payee")
    transaction_hash = _canonical_tx_hash(proof["transaction_hash"])
    amount_atomic = _canonical_amount(proof["amount_atomic"])
    if (
        token != network_record["token"]
        or payee != network_record["payee"]
    ):
        raise RelayerGateError("payment proof token or payee does not match network")
    return {
        "tenant_id": tenant_id,
        "purchase_id": purchase_id,
        "owner": owner,
        "transaction_hash": transaction_hash,
        "amount_atomic": amount_atomic,
    }


def _validate_abort_proof(proof: Mapping[str, object]) -> dict[str, str]:
    if not isinstance(proof, Mapping):
        raise RelayerGateError("unsubmitted proof must be an object")
    expected_keys = {
        "tenant_id",
        "purchase_id",
        "owner",
        "no_broadcast",
        "source",
    }
    if set(proof) != expected_keys:
        raise RelayerGateError("unsubmitted proof schema is not exact")
    if proof["no_broadcast"] is not True:
        raise RelayerGateError("unsubmitted proof must explicitly deny broadcast")
    if proof["source"] != "core_funding_ledger":
        raise RelayerGateError("unsubmitted proof source is not trusted")
    return {
        "tenant_id": _safe_identifier(proof["tenant_id"], label="proof tenant_id"),
        "purchase_id": _safe_identifier(
            proof["purchase_id"], label="proof purchase_id"
        ),
        "owner": _canonical_owner(proof["owner"], label="proof owner"),
    }


def _file_error(label: str, detail: str) -> RelayerGateError:
    return RelayerGateError(f"{label} {detail}")


class RelayerGate:
    """Own a durable, process-locked Monad relayer admission gate.

    ``state_dir`` is private gate state, ``network`` must be the validated
    Monad testnet ``NetworkConfig`` and ``relayer`` is the fixed transaction
    sender.  The gate never accepts a caller-supplied network or relayer after
    construction and never contacts an RPC or signer.
    """

    def __init__(self, state_dir: str | os.PathLike[str], network: NetworkConfig, relayer: str):
        if not isinstance(network, NetworkConfig):
            raise RelayerGateError("network must be a validated NetworkConfig")
        if network.mode != "monad_testnet" or network.chain_id != _MONAD_CHAIN_ID:
            raise RelayerGateError("relayer gate requires Monad chain 10143")
        self.state_dir = Path(state_dir)
        self._network = network
        self._relayer = _canonical_owner(relayer, label="relayer")
        self._network_record = self._network_to_record(network)
        self._journal_path = self.state_dir / _JOURNAL_NAME
        self._lock_path = self.state_dir / _LOCK_NAME
        self._initialize()

    @property
    def network(self) -> NetworkConfig:
        return self._network

    @property
    def relayer(self) -> str:
        return self._relayer

    @staticmethod
    def _network_to_record(network: NetworkConfig) -> dict[str, object]:
        return {
            "mode": network.mode,
            "chain_id": network.chain_id,
            "rpc_urls": list(network.rpc_urls),
            "token": network.token,
            "executor": network.executor,
            "payee": network.payee,
            "token_decimals": network.token_decimals,
            "gas_limit": network.gas_limit,
            "max_gas_price_wei": network.max_gas_price_wei,
        }

    def _initialize(self) -> None:
        fd = self._acquire_lock()
        try:
            if not self._path_exists(self._journal_path):
                self._write_journal(self._empty_journal())
            else:
                self._read_journal()
        finally:
            self._release_lock(fd)

    @staticmethod
    def _path_exists(path: Path) -> bool:
        try:
            os.lstat(path)
        except FileNotFoundError:
            return False
        return True

    def _validate_state_dir(self) -> None:
        try:
            info = os.lstat(self.state_dir)
        except FileNotFoundError:
            try:
                self.state_dir.mkdir(mode=_DIRECTORY_MODE)
            except FileExistsError:
                info = os.lstat(self.state_dir)
            else:
                info = os.lstat(self.state_dir)
        if stat.S_ISLNK(info.st_mode):
            raise _file_error("state directory", "must not be a symlink")
        if not stat.S_ISDIR(info.st_mode):
            raise _file_error("state directory", "must be a directory")
        if stat.S_IMODE(info.st_mode) != _DIRECTORY_MODE:
            raise _file_error("state directory", "must have mode 0700")

    def _validate_private_file(self, path: Path, label: str, *, create: bool) -> None:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            if not create:
                raise _file_error(label, "is missing")
            try:
                fd = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                    _FILE_MODE,
                )
            except FileExistsError:
                pass
            except OSError as exc:
                raise _file_error(label, f"could not be created: {exc}") from exc
            else:
                os.close(fd)
            try:
                info = os.lstat(path)
            except OSError as exc:
                raise _file_error(label, f"could not be inspected: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise _file_error(label, "must not be a symlink")
        if not stat.S_ISREG(info.st_mode):
            raise _file_error(label, "must be a regular file")
        if stat.S_IMODE(info.st_mode) != _FILE_MODE:
            raise _file_error(label, "must have mode 0600")
        if info.st_nlink != 1:
            raise _file_error(label, "must not be a hard link")

    def _acquire_lock(self) -> int:
        self._validate_state_dir()
        self._validate_private_file(self._lock_path, "lock file", create=True)
        try:
            fd = os.open(self._lock_path, os.O_RDWR | _NOFOLLOW)
        except OSError as exc:
            raise _file_error("lock file", f"could not be opened: {exc}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise _file_error("lock file", "must be a regular file")
            if stat.S_IMODE(info.st_mode) != _FILE_MODE or info.st_nlink != 1:
                raise _file_error("lock file", "must be a private single-link 0600 file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                    raise RelayerGateBusy("relayer gate lock is already held") from exc
                raise _file_error("lock file", f"could not be locked: {exc}") from exc
            return fd
        except Exception:
            os.close(fd)
            raise

    @staticmethod
    def _release_lock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _empty_journal(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "network": self._network_record,
            "relayer": self.relayer,
            "active": None,
            "last_completed": None,
        }

    def _read_journal(self) -> dict[str, object]:
        self._validate_state_dir()
        self._validate_private_file(self._journal_path, "journal", create=False)
        try:
            fd = os.open(self._journal_path, os.O_RDONLY | _NOFOLLOW)
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or stat.S_IMODE(info.st_mode) != _FILE_MODE
                    or info.st_nlink != 1
                ):
                    raise _file_error("journal", "must be a private single-link 0600 file")
                raw = b""
                while True:
                    chunk = os.read(fd, 64 * 1024)
                    if not chunk:
                        break
                    raw += chunk
                    if len(raw) > 1_000_000:
                        raise _file_error("journal", "is too large")
            finally:
                os.close(fd)
        except RelayerGateError:
            raise
        except (OSError, UnicodeDecodeError) as exc:
            raise _file_error("journal", f"could not be read: {exc}") from exc
        try:
            journal = json.loads(
                raw.decode("utf-8"),
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except RelayerGateError:
            raise
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise _file_error("journal", f"is corrupt: {exc}") from exc
        self._validate_journal(journal)
        return journal

    def _validate_journal(self, journal: object) -> None:
        if not isinstance(journal, dict):
            raise _file_error("journal", "root must be an object")
        expected_keys = {
            "schema_version",
            "network",
            "relayer",
            "active",
            "last_completed",
        }
        if set(journal) != expected_keys:
            raise _file_error("journal", "schema is not exact")
        if journal["schema_version"] != _SCHEMA_VERSION:
            raise _file_error("journal", "schema version is unsupported")
        if journal["network"] != self._network_record:
            raise _file_error("journal", "network does not match configured network")
        if journal["relayer"] != self.relayer:
            raise _file_error("journal", "relayer does not match configured relayer")
        self._validate_active(journal["active"])
        self._validate_last_completed(journal["last_completed"])
        if journal["active"] is not None and journal["last_completed"] is not None:
            active = journal["active"]
            completed = journal["last_completed"]
            if (
                active["tenant_id"] == completed["tenant_id"]
                and active["purchase_id"] == completed["purchase_id"]
            ):
                raise _file_error("journal", "active pair already has a completion")

    @staticmethod
    def _validate_active(active: object) -> None:
        if active is None:
            return
        if not isinstance(active, dict) or set(active) != {"tenant_id", "purchase_id", "owner"}:
            raise _file_error("journal", "active state is invalid")
        _safe_identifier(active["tenant_id"], label="active tenant_id")
        _safe_identifier(active["purchase_id"], label="active purchase_id")
        owner = _canonical_owner(active["owner"], label="active owner")
        if active["owner"] != owner:
            raise _file_error("journal", "active owner is not canonical")

    @staticmethod
    def _validate_last_completed(completed: object) -> None:
        if completed is None:
            return
        if not isinstance(completed, dict) or set(completed) != {
            "tenant_id",
            "purchase_id",
            "owner",
            "transaction_hash",
            "amount_atomic",
        }:
            raise _file_error("journal", "last completion is invalid")
        _safe_identifier(completed["tenant_id"], label="completed tenant_id")
        _safe_identifier(completed["purchase_id"], label="completed purchase_id")
        owner = _canonical_owner(completed["owner"], label="completed owner")
        if completed["owner"] != owner:
            raise _file_error("journal", "completed owner is not canonical")
        _canonical_tx_hash(completed["transaction_hash"])
        _canonical_amount(completed["amount_atomic"])

    def _write_journal(self, journal: dict[str, object]) -> None:
        self._validate_journal(journal)
        # Validate before staging a replacement so a permissions/type error can
        # never destroy the last durable active pair.
        self._validate_private_file(self._journal_path, "journal", create=True)
        payload = json.dumps(journal, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        temp_path: str | None = None
        fd: int | None = None
        try:
            fd, temp_path = tempfile.mkstemp(
                prefix=f".{_JOURNAL_NAME}.",
                dir=self.state_dir,
            )
            os.fchmod(fd, _FILE_MODE)
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != _FILE_MODE
                or info.st_nlink != 1
            ):
                raise _file_error("journal", "must be a private single-link 0600 file")
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise _file_error("journal", "write made no progress")
                view = view[written:]
            os.fsync(fd)
            os.close(fd)
            fd = None
            os.replace(temp_path, self._journal_path)
            temp_path = None
            self._validate_private_file(self._journal_path, "journal", create=False)
            directory_fd = os.open(self.state_dir, os.O_RDONLY | _NOFOLLOW)
            try:
                directory_info = os.fstat(directory_fd)
                if not stat.S_ISDIR(directory_info.st_mode):
                    raise _file_error("state directory", "must be a directory")
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except RelayerGateError:
            raise
        except OSError as exc:
            raise _file_error("journal", f"could not be written: {exc}") from exc
        finally:
            if fd is not None:
                os.close(fd)
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def enter(self, tenant_id: str, purchase_id: str, owner: str) -> "RelayerLease":
        """Return a context-managed lease for one opaque tenant/purchase pair."""
        return RelayerLease(
            self,
            _safe_identifier(tenant_id, label="tenant_id"),
            _safe_identifier(purchase_id, label="purchase_id"),
            _canonical_owner(owner),
        )

    def reconcile_verified_payment(
        self, proof: Mapping[str, object]
    ) -> dict[str, str]:
        """Reconcile a verified payment after a relayer process crash.

        The caller must supply the same strict proof accepted by an active
        lease.  Reconciliation only clears an active pair when the proof
        scope matches it exactly; a late proof can never clear another pair.
        """
        checked = _validate_payment_proof(proof, self._network_record)
        fd = self._acquire_lock()
        try:
            journal = self._read_journal()
            active = journal["active"]
            if active is None:
                return {"status": "already_clear"}
            if (
                checked["tenant_id"] != active["tenant_id"]
                or checked["purchase_id"] != active["purchase_id"]
                or checked["owner"] != active["owner"]
            ):
                return {"status": "different_active"}
            completion = {
                "tenant_id": active["tenant_id"],
                "purchase_id": active["purchase_id"],
                "owner": active["owner"],
                "transaction_hash": checked["transaction_hash"],
                "amount_atomic": checked["amount_atomic"],
            }
            journal["active"] = None
            journal["last_completed"] = completion
            self._write_journal(journal)
            return {"status": "reconciled", **completion}
        finally:
            self._release_lock(fd)


class RelayerLease:
    """A lock-held relayer lease; exiting without completion retains active state."""

    def __init__(self, gate: RelayerGate, tenant_id: str, purchase_id: str, owner: str):
        self.gate = gate
        self.tenant_id = tenant_id
        self.purchase_id = purchase_id
        self.owner = owner
        self._fd: int | None = None
        self._completed = False
        self._aborted = False

    def __enter__(self) -> "RelayerLease":
        if self._fd is not None:
            raise RelayerGateError("relayer lease is already entered")
        fd = self.gate._acquire_lock()
        try:
            journal = self.gate._read_journal()
            active = journal["active"]
            if active is not None:
                if (
                    active["tenant_id"] != self.tenant_id
                    or active["purchase_id"] != self.purchase_id
                    or active["owner"] != self.owner
                ):
                    raise RelayerGateError("a different active relayer pair must complete first")
            else:
                completed = journal["last_completed"]
                if completed is not None and (
                    completed["tenant_id"] == self.tenant_id
                    and completed["purchase_id"] == self.purchase_id
                ):
                    raise RelayerGateError("purchase pair already completed")
                journal["active"] = {
                    "tenant_id": self.tenant_id,
                    "purchase_id": self.purchase_id,
                    "owner": self.owner,
                }
                self.gate._write_journal(journal)
            self._fd = fd
            return self
        except Exception:
            self.gate._release_lock(fd)
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        fd, self._fd = self._fd, None
        if fd is not None:
            self.gate._release_lock(fd)
        return False

    def complete_verified_payment(self, proof: Mapping[str, object]) -> dict[str, str]:
        """Clear active state only for a strict, matching verified proof."""
        if self._fd is None:
            raise RelayerGateError("relayer lease is not active")
        if self._completed or self._aborted:
            raise RelayerGateError("relayer lease was already closed")
        checked = self._validate_proof(proof)
        journal = self.gate._read_journal()
        active = journal["active"]
        expected_active = {
            "tenant_id": self.tenant_id,
            "purchase_id": self.purchase_id,
            "owner": self.owner,
        }
        if active != expected_active:
            raise RelayerGateError("journal active pair does not match this lease")
        if (
            checked["tenant_id"] != self.tenant_id
            or checked["purchase_id"] != self.purchase_id
            or checked["owner"] != self.owner
        ):
            raise RelayerGateError("payment proof does not match active relayer pair")
        completion = {
            "tenant_id": self.tenant_id,
            "purchase_id": self.purchase_id,
            "owner": self.owner,
            "transaction_hash": checked["transaction_hash"],
            "amount_atomic": checked["amount_atomic"],
        }
        journal["active"] = None
        journal["last_completed"] = completion
        self.gate._write_journal(journal)
        self._completed = True
        return dict(completion)

    def abort_unsubmitted(self, proof: Mapping[str, object]) -> dict[str, str]:
        """Clear this lease only with trusted Core proof of no broadcast."""
        if self._fd is None:
            raise RelayerGateError("relayer lease is not active")
        if self._completed or self._aborted:
            raise RelayerGateError("relayer lease was already closed")
        checked = _validate_abort_proof(proof)
        if (
            checked["tenant_id"] != self.tenant_id
            or checked["purchase_id"] != self.purchase_id
            or checked["owner"] != self.owner
        ):
            raise RelayerGateError("unsubmitted proof does not match active relayer pair")
        journal = self.gate._read_journal()
        expected_active = {
            "tenant_id": self.tenant_id,
            "purchase_id": self.purchase_id,
            "owner": self.owner,
        }
        if journal["active"] != expected_active:
            raise RelayerGateError("journal active pair does not match this lease")
        journal["active"] = None
        self.gate._write_journal(journal)
        self._aborted = True
        return {"status": "aborted"}

    def _validate_proof(self, proof: Mapping[str, object]) -> dict[str, str]:
        return _validate_payment_proof(proof, self.gate._network_record)
