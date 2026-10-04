"""Build a deterministic, read-only Monad deployment plan.

The planner prepares the two contract-creation transactions needed by the
local TestUSD composition.  It never signs, broadcasts, loads AWS credentials,
or contacts an RPC unless the opt-in ``--rpc-check`` path is used.  The RPC
path is read-only and exists only to compare the explicit nonce, gas, chain,
and funding observations before a separate operator decides what to do.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from eth_abi import encode
from eth_utils import keccak, to_checksum_address
import rlp


ROOT = Path(__file__).resolve().parents[2]

CHAIN_ID = 10143
BUYER = "0x59899831691aa79507818961773497c751bffc8b"
DEPLOYER = "0xBdCb39Ac5Ff83485cb35160F0DdAA0b7446Ee009"
EXECUTION_SIGNER = "0x968DbAbb8Dca19C4A8174A260Cc40Db66BdB7915"
SUPPLY = 1_000_000
TOKEN_DECIMALS = 6
MAX_GAS_PRICE_WEI = 500_000_000_000
MAX_TX_GAS_LIMIT = 2_000_000
DEFAULT_TX_GAS_LIMIT = MAX_TX_GAS_LIMIT

RPC_URLS = (
    "https://testnet-rpc.monad.xyz",
    "https://rpc-testnet.monadinfra.com",
)

_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_HEX_RE = re.compile(r"0x[0-9a-fA-F]*\Z")

# Kept as a patchable capability for fake-RPC tests.  Importing this module is
# intentionally side-effect free; the real client is loaded only on demand.
RpcClient: Any = None


def _strict_int(value: object, *, name: str, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return value


def _address(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _ADDRESS_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a 20-byte EVM address")
    normalized = value.lower()
    if int(normalized, 16) == 0:
        raise ValueError(f"{name} must be nonzero")
    return normalized


def _rlp_nonce(nonce: int) -> bytes:
    return b"" if nonce == 0 else nonce.to_bytes((nonce.bit_length() + 7) // 8, "big")


def predict_create_address(sender: str, nonce: int) -> str:
    """Return the lower-case address for ``CREATE(sender, nonce)``."""

    sender = _address(sender, name="sender")
    nonce = _strict_int(nonce, name="nonce", minimum=0)
    encoded = rlp.encode([bytes.fromhex(sender[2:]), _rlp_nonce(nonce)])
    return "0x" + keccak(encoded)[-20:].hex()


def _artifact(root: Path, name: str) -> dict[str, Any]:
    relative_path = Path("contracts") / "out" / f"{name}.sol" / f"{name}.json"
    path = root / relative_path
    try:
        document_bytes = path.read_bytes()
        document = json.loads(document_bytes)
        bytecode_object = document["bytecode"]["object"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(f"local artifact is missing or invalid: {relative_path}") from None

    if not isinstance(bytecode_object, str) or _HEX_RE.fullmatch(bytecode_object) is None:
        raise ValueError(f"local artifact bytecode is invalid: {relative_path}")
    if bytecode_object == "0x" or len(bytecode_object) % 2 != 0:
        raise ValueError(f"local artifact bytecode is empty or malformed: {relative_path}")
    try:
        bytecode = bytes.fromhex(bytecode_object[2:])
    except ValueError:
        raise ValueError(f"local artifact bytecode is malformed: {relative_path}") from None
    if not bytecode:
        raise ValueError(f"local artifact bytecode is empty: {relative_path}")

    bytecode_sha256 = hashlib.sha256(bytecode).hexdigest()
    bytecode_keccak = "0x" + keccak(bytecode).hex()
    return {
        "name": name,
        "path": relative_path.as_posix(),
        "artifact_sha256": hashlib.sha256(document_bytes).hexdigest(),
        "bytecode_sha256": bytecode_sha256,
        "creation_bytecode_sha256": bytecode_sha256,
        "bytecode_keccak256": bytecode_keccak,
        "creation_bytecode_keccak256": bytecode_keccak,
        "bytecode_length": len(bytecode),
        "bytecode": bytecode,
    }


def _encoded_constructor(types: list[str], values: list[object]) -> str:
    return "0x" + encode(types, values).hex()


def _transaction(
    *,
    nonce: int,
    data: bytes,
    constructor_arguments: dict[str, object],
    constructor_types: list[str],
    constructor_encoded: str,
    gas_price_wei: int,
    gas_limit: int,
    init_code_sha256: str,
    init_code_keccak256: str,
) -> dict[str, object]:
    data_hex = "0x" + data.hex()
    return {
        # The KMS rail accepts EIP-155 legacy transactions only.  Keep this
        # explicit in the plan so a downstream operator cannot infer a typed
        # EIP-1559 transaction from fee metadata.
        "type": "legacy",
        "chain_id": CHAIN_ID,
        "chainId": CHAIN_ID,
        "nonce": nonce,
        "from": DEPLOYER,
        "to": None,
        "value": 0,
        "data": data_hex,
        "gas_limit": gas_limit,
        "gas": gas_limit,
        "gasPrice": gas_price_wei,
        "constructor_arguments": constructor_arguments,
        "constructor_types": constructor_types,
        "constructor_data": constructor_encoded,
        "init_code_sha256": init_code_sha256,
        "init_code_keccak256": init_code_keccak256,
    }


def _canonical_hash(value: dict[str, object]) -> str:
    payload = dict(value)
    # These fields are attached or updated by read-only checks after the
    # deterministic signing data is built.  Excluding them keeps the digest
    # stable when an operator records RPC observations or a blocked status.
    for runtime_field in ("plan_sha256", "status", "rpc_check"):
        payload.pop(runtime_field, None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def build_plan(
    *,
    nonce: int,
    gas_price_wei: int,
    payee: str,
    gas_limit: int = DEFAULT_TX_GAS_LIMIT,
    root: Path = ROOT,
) -> dict[str, object]:
    """Build a deterministic two-transaction creation plan.

    ``nonce`` is the exact deployer pending nonce supplied by the operator.  A
    caller may use ``read_rpc_check`` to compare it to two read-only RPC
    observations before handing this plan to a separately controlled signer.
    """

    nonce = _strict_int(nonce, name="nonce", minimum=0)
    gas_price_wei = _strict_int(
        gas_price_wei,
        name="gas_price_wei",
        minimum=1,
        maximum=MAX_GAS_PRICE_WEI,
    )
    gas_limit = _strict_int(
        gas_limit,
        name="gas_limit",
        minimum=1,
        maximum=MAX_TX_GAS_LIMIT,
    )
    root = Path(root)
    buyer = _address(BUYER, name="buyer")
    deployer = _address(DEPLOYER, name="deployer")
    execution_signer = _address(EXECUTION_SIGNER, name="execution_signer")
    payee = _address(payee, name="payee")
    if payee == buyer or payee == execution_signer:
        raise ValueError("payee must be distinct from buyer and execution signer addresses")

    token_artifact = _artifact(root, "AgentonomyTestUSD")
    executor_artifact = _artifact(root, "AgentonomyBudgetExecutor")
    token_address = predict_create_address(deployer, nonce)
    executor_address = predict_create_address(deployer, nonce + 1)
    if payee in {token_address, executor_address}:
        raise ValueError("payee must be distinct from predicted contract addresses")

    token_constructor = _encoded_constructor(["address", "uint256"], [buyer, SUPPLY])
    token_init_code = token_artifact["bytecode"] + bytes.fromhex(token_constructor[2:])
    executor_constructor = _encoded_constructor(["address"], [token_address])
    executor_init_code = executor_artifact["bytecode"] + bytes.fromhex(executor_constructor[2:])

    transactions = [
        _transaction(
            nonce=nonce,
            data=token_init_code,
            constructor_arguments={"owner": buyer, "supply": SUPPLY},
            constructor_types=["address", "uint256"],
            constructor_encoded=token_constructor,
            gas_price_wei=gas_price_wei,
            gas_limit=gas_limit,
            init_code_sha256=hashlib.sha256(token_init_code).hexdigest(),
            init_code_keccak256="0x" + keccak(token_init_code).hex(),
        ),
        _transaction(
            nonce=nonce + 1,
            data=executor_init_code,
            constructor_arguments={"tokenAddress": token_address},
            constructor_types=["address"],
            constructor_encoded=executor_constructor,
            gas_price_wei=gas_price_wei,
            gas_limit=gas_limit,
            init_code_sha256=hashlib.sha256(executor_init_code).hexdigest(),
            init_code_keccak256="0x" + keccak(executor_init_code).hex(),
        ),
    ]

    max_fee_wei = gas_limit * gas_price_wei
    plan: dict[str, object] = {
        "schema_version": "monad-deployment-plan-v1",
        "status": "planned",
        "broadcast": False,
        "signed": False,
        "chain_id": CHAIN_ID,
        "network": f"eip155:{CHAIN_ID}",
        "nonce": nonce,
        "fee_model": "legacy_eip155",
        "gas_price_wei": gas_price_wei,
        "gas_price_cap_wei": gas_price_wei,
        "gas_limit": gas_limit,
        "max_fee_per_gas_wei": gas_price_wei,
        "max_fee_wei_per_transaction": max_fee_wei,
        "required_funding_wei": max_fee_wei * 2,
        "buyer": buyer,
        "supply": SUPPLY,
        "token_decimals": TOKEN_DECIMALS,
        "deployer": DEPLOYER,
        "gas_address": DEPLOYER,
        "execution_signer": EXECUTION_SIGNER,
        "payee": payee,
        "public_addresses": {
            "buyer": buyer,
            "deployer_gas": DEPLOYER,
            "execution_signer": EXECUTION_SIGNER,
            "payee": payee,
        },
        "key_roles": ["deployer_gas", "execution_signer"],
        "rpc_urls": list(RPC_URLS),
        "predicted_addresses": {
            "token": token_address,
            "executor": executor_address,
        },
        "predicted_addresses_checksum": {
            "token": to_checksum_address(token_address),
            "executor": to_checksum_address(executor_address),
        },
        "artifacts": {
            key: {field: value for field, value in artifact.items() if field != "bytecode"}
            for key, artifact in (
                ("AgentonomyTestUSD", token_artifact),
                ("AgentonomyBudgetExecutor", executor_artifact),
            )
        },
        "transactions": transactions,
        "assumptions": {
            "creation_address_formula": "keccak256(rlp.encode([deployer, nonce]))[-20:]",
            "asset": "AgentonomyTestUSD; six decimals; no monetary value",
            "max_fee_is_cap": True,
            "broadcast_allowed": False,
        },
    }
    plan["constructor_arguments"] = {
        "AgentonomyTestUSD": transactions[0]["constructor_arguments"],
        "AgentonomyBudgetExecutor": transactions[1]["constructor_arguments"],
    }
    plan["plan_sha256"] = _canonical_hash(plan)
    return plan


def _quantity(value: object, *, name: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)\Z", value) is None:
        raise ValueError(f"invalid {name} RPC quantity")
    return int(value, 16)


def _rpc_error(url: str) -> dict[str, str]:
    # Do not serialize provider exception details or credentials into a plan.
    return {"url": url, "reason": "rpc_read_failed"}


def read_rpc_check(
    plan: dict[str, object],
    *,
    rpc_factory: Callable[[str], object] | None = None,
) -> dict[str, object]:
    """Read both configured RPCs and report disagreements without mutation."""

    if rpc_factory is None:
        # Import lazily so importing/building a dry-run plan does not establish
        # any network client or alter the module's no-RPC default.
        global RpcClient
        if RpcClient is None:
            from agentonomy_commerce.budget_network import RpcClient as _RpcClient

            RpcClient = _RpcClient

        rpc_factory = RpcClient

    urls = plan.get("rpc_urls")
    if not isinstance(urls, list) or len(urls) != 2 or any(not isinstance(url, str) for url in urls):
        raise ValueError("plan must contain exactly two RPC URLs")
    # Reuse the network boundary's URL validation so credentials, queries,
    # fragments, non-HTTPS schemes, and malformed hosts are rejected before a
    # client is constructed.  Normalization also keeps the returned report
    # free of trailing-slash variants.
    from agentonomy_commerce.budget_network import rpc_url as validate_rpc_url

    urls = [validate_rpc_url(url, local=False) for url in urls]
    hosts = [urlsplit(url).hostname.lower() for url in urls]
    if len(set(hosts)) != 2:
        raise ValueError("RPC URLs must use distinct hosts")

    observations: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for url in urls:
        try:
            client = rpc_factory(url)
            client.check_chain(CHAIN_ID)  # type: ignore[attr-defined]
            pending_nonce = _quantity(
                client.call("eth_getTransactionCount", [DEPLOYER.lower(), "pending"]),  # type: ignore[attr-defined]
                name="pending nonce",
            )
            gas_price_wei = _quantity(client.call("eth_gasPrice", []), name="gas price")  # type: ignore[attr-defined]
            balance_wei = _quantity(
                client.call("eth_getBalance", [DEPLOYER.lower(), "latest"]),  # type: ignore[attr-defined]
                name="balance",
            )
            observations.append(
                {
                    "url": url,
                    "chain_id": CHAIN_ID,
                    "pending_nonce": pending_nonce,
                    "gas_price_wei": gas_price_wei,
                    "balance_wei": balance_wei,
                }
            )
        except Exception:
            errors.append(_rpc_error(url))

    reasons: list[str] = []
    rpc_disagreement = False
    if errors:
        reasons.append("rpc_error")
    if len(observations) == 2:
        nonce_values = {item["pending_nonce"] for item in observations}
        gas_values = {item["gas_price_wei"] for item in observations}
        balance_values = {item["balance_wei"] for item in observations}
        if len(nonce_values) != 1:
            reasons.append("pending_nonce")
            rpc_disagreement = True
        if len(gas_values) != 1:
            reasons.append("gas_price")
            rpc_disagreement = True
        if len(balance_values) != 1:
            reasons.append("balance")
            rpc_disagreement = True

        if next(iter(nonce_values)) != plan.get("nonce"):
            reasons.append("planned_nonce")
            rpc_disagreement = True
        if any(
            type(item["gas_price_wei"]) is not int
            or item["gas_price_wei"] <= 0
            or item["gas_price_wei"] > MAX_GAS_PRICE_WEI
            for item in observations
        ):
            reasons.append("gas_price_cap")
        concrete_gas_price = plan.get("gas_price_wei")
        if type(concrete_gas_price) is not int or concrete_gas_price <= 0:
            raise ValueError("plan gas price is invalid")
        if any(item["gas_price_wei"] > concrete_gas_price for item in observations):
            reasons.append("gas_price_above_plan")
        required_funding = plan.get("required_funding_wei")
        if type(required_funding) is not int:
            raise ValueError("plan funding cap is invalid")
        insufficient_funding = any(item["balance_wei"] < required_funding for item in observations)
        if insufficient_funding:
            reasons.append("insufficient_funding")
    else:
        insufficient_funding = False

    # Preserve order while avoiding duplicate reason labels when multiple
    # checks identify the same underlying problem.
    reasons = list(dict.fromkeys(reasons))
    return {
        "status": "ready" if not reasons else "blocked",
        "rpc_urls": urls,
        "observations": observations,
        "errors": errors,
        "rpc_disagreement": rpc_disagreement,
        "insufficient_funding": insufficient_funding,
        "required_funding_wei": plan.get("required_funding_wei"),
        "reasons": reasons,
        "broadcast": False,
        "signed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nonce", required=True, type=int)
    parser.add_argument("--gas-price-wei", required=True, type=int)
    parser.add_argument("--payee", required=True)
    parser.add_argument("--gas-limit", type=int, default=DEFAULT_TX_GAS_LIMIT)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--rpc-check", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = build_plan(
            nonce=args.nonce,
            gas_price_wei=args.gas_price_wei,
            payee=args.payee,
            gas_limit=args.gas_limit,
            root=args.root,
        )
        if args.rpc_check:
            plan["rpc_check"] = read_rpc_check(plan)
            if plan["rpc_check"]["status"] == "blocked":  # type: ignore[index]
                plan["status"] = "blocked"
    except (OSError, ValueError, TypeError) as exc:
        plan = {
            "status": "blocked",
            "broadcast": False,
            "signed": False,
            "reason": str(exc),
        }
    print(json.dumps(plan, sort_keys=True, indent=2))
    return 2 if plan.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
