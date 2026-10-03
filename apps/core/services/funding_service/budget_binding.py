"""Durable binding between a Core SpendingGrant and a chain SpendGrant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Mapping

from eth_account import Account
from eth_account._utils.legacy_transactions import Transaction
from eth_utils import keccak
import rlp

from services.account_service.schemas import canonicalize_evm_address


_BYTES32 = re.compile(r"^0x[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^0x[0-9a-f]{130}$")
_UINT256_MAX = 2**256 - 1


def _protocol() -> tuple[type, Any, Any, Any]:
    """Load the shared facilitator protocol without duplicating its models."""

    try:
        from budget_protocol import (  # type: ignore
            SpendGrant,
            hash_grant,
            recover_grant_signer,
        )
    except ImportError:
        from apps.facilitator.budget_protocol import (  # type: ignore
            SpendGrant,
            hash_grant,
            recover_grant_signer,
        )
    return SpendGrant, hash_grant, recover_grant_signer, None


def _execution_protocol() -> tuple[type, Any, Any, Any]:
    """Load deterministic execution value helpers without importing transport code."""

    try:
        from budget_protocol import (  # type: ignore
            PurchaseExecution,
            encode_execute_calldata_hex,
            hash_execution,
            verify_execution_signature,
        )
    except ImportError:
        from apps.facilitator.budget_protocol import (  # type: ignore
            PurchaseExecution,
            encode_execute_calldata_hex,
            hash_execution,
            verify_execution_signature,
        )
    return PurchaseExecution, hash_execution, verify_execution_signature, encode_execute_calldata_hex


def _clean_identifier(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"{field_name} is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} contains control characters")
    return value


def canonical_bytes32(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _BYTES32.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lower-case bytes32")
    return value


def canonical_signature(value: object) -> str:
    if isinstance(value, bytes):
        value = "0x" + value.hex()
    if not isinstance(value, str):
        raise ValueError("owner_signature must be a 65-byte signature")
    value = value.lower()
    if _SIGNATURE.fullmatch(value) is None:
        raise ValueError("owner_signature must be a 65-byte signature")
    return value


def _grant_value(grant: Any, snake: str, camel: str) -> Any:
    value = getattr(grant, snake, None)
    if value is not None:
        return value
    value = getattr(grant, camel, None)
    if value is not None:
        return value
    if isinstance(grant, Mapping):
        return grant.get(snake, grant.get(camel))
    return None


def _canonical_grant_payload(grant: Any) -> dict[str, Any]:
    if isinstance(grant, Mapping):
        payload = dict(grant)
    elif hasattr(grant, "to_json"):
        payload = dict(grant.to_json())
    elif hasattr(grant, "model_dump"):
        payload = dict(grant.model_dump(mode="json"))
    else:
        raise ValueError("budget SpendGrant is invalid")
    return payload


def _grant_model(grant: Any) -> Any:
    SpendGrant, _hash_grant, _recover, _ = _protocol()
    if isinstance(grant, SpendGrant):
        return grant
    payload = _canonical_grant_payload(grant)
    try:
        return SpendGrant.from_json(payload)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("budget SpendGrant is invalid") from exc


def _digest_hex(value: bytes | bytearray | str) -> str:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, str):
        return value if value.startswith("0x") else "0x" + value
    raise ValueError("budget digest is invalid")


def derive_agent_scope(agent_id: str, spending_grant_id: str) -> str:
    """Return the canonical scope commitment used by the budget protocol.

    The domain tag and length-delimited UTF-8 components commit the Core agent
    and exact Core grant ID without depending on JSON or locale formatting.
    """

    agent_id = _clean_identifier(agent_id, field_name="agent_id")
    spending_grant_id = _clean_identifier(
        spending_grant_id, field_name="spending_grant_id"
    )
    encoded = (
        b"clink-budget-agent-scope-v1\0"
        + len(agent_id.encode("utf-8")).to_bytes(4, "big")
        + agent_id.encode("utf-8")
        + len(spending_grant_id.encode("utf-8")).to_bytes(4, "big")
        + spending_grant_id.encode("utf-8")
    )
    return "0x" + keccak(encoded).hex()


def purchase_commitment(purchase_id: str) -> str:
    """Return the canonical purchase commitment used by the chain protocol."""

    purchase_id = _clean_identifier(purchase_id, field_name="purchase_id")
    return "0x" + keccak(b"agentonomy:purchase:v1:" + purchase_id.encode("utf-8")).hex()


def binding_id_for(spending_grant_id: str) -> str:
    """Derive a stable local record ID for the one-to-one Core grant binding."""

    spending_grant_id = _clean_identifier(
        spending_grant_id, field_name="spending_grant_id"
    )
    return "budget_binding_" + hashlib.sha256(
        ("clink-budget-binding-v1\0" + spending_grant_id).encode("utf-8")
    ).hexdigest()[:32]


def protocol_grant_hash(grant: Any, chain_id: int, executor_contract: str) -> str:
    model = _grant_model(grant)
    _SpendGrant, hash_grant, _recover, _ = _protocol()
    try:
        digest = hash_grant(model, chain_id, executor_contract)
    except (TypeError, ValueError) as exc:
        raise ValueError("budget SpendGrant digest could not be computed") from exc
    result = canonical_bytes32(_digest_hex(digest), field_name="grant_hash")
    return result


def verify_owner_signature(
    grant: Any,
    owner_signature: str | bytes,
    *,
    chain_id: int,
    executor_contract: str,
    owner: str,
) -> str:
    """Verify the full owner signature against the exact EIP-712 domain."""

    model = _grant_model(grant)
    signature = canonical_signature(owner_signature)
    _SpendGrant, _hash_grant, recover_grant_signer, _ = _protocol()
    try:
        recovered = recover_grant_signer(model, signature, chain_id, executor_contract)
        recovered = canonicalize_evm_address(recovered)
    except Exception as exc:
        # Protocol implementations intentionally use a protocol-specific error;
        # do not leak its details or accept a partially recovered signer.
        raise ValueError("budget owner signature is invalid") from exc
    expected = canonicalize_evm_address(owner)
    if recovered != expected:
        raise ValueError("budget owner signature does not match wallet identity")
    return signature


@dataclass(frozen=True)
class BudgetBinding:
    binding_id: str
    spending_grant_id: str
    wallet_identity_id: str
    user_id: str
    agent_id: str
    owner: str
    agent_scope: str
    network: str
    chain_id: int
    executor_contract: str
    token: str
    token_symbol: str
    token_decimals: int
    payee: str
    max_per_payment: int
    max_total: int
    valid_after: int
    valid_until: int
    execution_signer: str
    grant_id: str
    grant_hash: str
    owner_signature: str
    grant_payload: dict[str, Any]
    created_at: str
    status: str = "active"

    @property
    def grant(self) -> Any:
        return _grant_model(self.grant_payload)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "BudgetBinding":
        values = dict(record)
        payload = values.get("grant_payload") or values.get("grant")
        if not isinstance(payload, Mapping):
            raise ValueError("stored budget binding grant is invalid")
        values["grant_payload"] = dict(payload)
        values.pop("grant", None)
        for field_name in (
            "binding_id",
            "spending_grant_id",
            "wallet_identity_id",
            "user_id",
            "agent_id",
            "network",
            "token_symbol",
            "created_at",
            "status",
        ):
            values[field_name] = _clean_identifier(
                values.get(field_name), field_name=field_name
            )
        for field_name in (
            "owner",
            "executor_contract",
            "token",
            "payee",
            "execution_signer",
        ):
            values[field_name] = canonicalize_evm_address(values.get(field_name))
        values["agent_scope"] = canonical_bytes32(
            values.get("agent_scope"), field_name="agent_scope"
        )
        values["grant_id"] = canonical_bytes32(
            values.get("grant_id"), field_name="grant_id"
        )
        values["grant_hash"] = canonical_bytes32(
            values.get("grant_hash"), field_name="grant_hash"
        )
        values["owner_signature"] = canonical_signature(values.get("owner_signature"))
        for field_name in (
            "chain_id",
            "token_decimals",
            "max_per_payment",
            "max_total",
            "valid_after",
            "valid_until",
        ):
            value = values.get(field_name)
            if type(value) is not int or value < 0 or value > _UINT256_MAX:
                raise ValueError(f"stored budget binding {field_name} is invalid")
        if values["valid_after"] >= values["valid_until"]:
            raise ValueError("stored budget binding validity window is invalid")
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "spending_grant_id": self.spending_grant_id,
            "wallet_identity_id": self.wallet_identity_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "owner": self.owner,
            "agent_scope": self.agent_scope,
            "network": self.network,
            "chain_id": self.chain_id,
            "executor_contract": self.executor_contract,
            "token": self.token,
            "token_symbol": self.token_symbol,
            "token_decimals": self.token_decimals,
            "payee": self.payee,
            "max_per_payment": self.max_per_payment,
            "max_total": self.max_total,
            "valid_after": self.valid_after,
            "valid_until": self.valid_until,
            "execution_signer": self.execution_signer,
            "grant_id": self.grant_id,
            "grant_hash": self.grant_hash,
            "owner_signature": self.owner_signature,
            "grant_payload": self.grant_payload,
            "created_at": self.created_at,
            "status": self.status,
        }

    def require_row_scope(self, row: Mapping[str, Any]) -> None:
        expected = {
            "network": self.network,
            "token_address": self.token,
            "destination": self.payee,
            "wallet_identity_id": self.wallet_identity_id,
            "spending_grant_id": self.spending_grant_id,
        }
        for field_name, expected_value in expected.items():
            actual = row.get(field_name)
            if field_name in {"token_address", "destination"}:
                try:
                    actual = canonicalize_evm_address(actual)
                except ValueError:
                    pass
            if actual != expected_value:
                raise ValueError(f"budget binding {field_name} does not match reservation")


def validate_signed_budget_attempt(
    attempt: Mapping[str, Any],
    row: Mapping[str, Any],
    binding: BudgetBinding,
    *,
    relayer: str,
    nonce: int,
    now_timestamp: int,
) -> dict[str, Any]:
    """Validate and normalize the complete durable budget transaction attempt.

    This is intentionally a pure protocol check.  It does not call the backend
    or compose calldata through the transport adapter.  Core journals only an
    attempt whose signed transaction, execution permit and signatures all bind
    to the immutable reservation and binding scope.
    """

    if not isinstance(attempt, Mapping):
        raise ValueError("budget prepared attempt is invalid")
    raw_transaction = attempt.get("raw_transaction")
    if not isinstance(raw_transaction, str) or not raw_transaction.startswith("0x"):
        raise ValueError("budget prepared raw transaction is invalid")
    raw_hex = raw_transaction[2:]
    if not raw_hex or len(raw_hex) % 2:
        raise ValueError("budget prepared raw transaction is invalid")
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError as exc:
        raise ValueError("budget prepared raw transaction is invalid") from exc

    tx_hash = canonical_bytes32(attempt.get("tx_hash"), field_name="tx_hash")
    if "0x" + keccak(raw).hex() != tx_hash:
        raise ValueError("budget prepared transaction hash does not match raw transaction")
    try:
        canonical_relayer = canonicalize_evm_address(relayer)
        attempt_relayer = canonicalize_evm_address(attempt.get("relayer"))
    except (TypeError, ValueError) as exc:
        raise ValueError("budget prepared transaction relayer is invalid") from exc
    if attempt_relayer != canonical_relayer or type(attempt.get("nonce")) is not int:
        raise ValueError("budget prepared transaction identity is invalid")
    if attempt["nonce"] != nonce:
        raise ValueError("budget prepared transaction identity is invalid")

    transaction = attempt.get("transaction")
    if not isinstance(transaction, Mapping):
        raise ValueError("budget prepared transaction projection is invalid")
    required_transaction_fields = (
        "chainId",
        "nonce",
        "from",
        "to",
        "value",
        "data",
        "gasPrice",
        "gas",
    )
    if any(field_name not in transaction for field_name in required_transaction_fields):
        raise ValueError("budget prepared transaction projection is incomplete")

    def uint_field(value: Any, field_name: str, *, positive: bool = False) -> int:
        if type(value) is not int or value < 0 or value > _UINT256_MAX:
            raise ValueError(f"budget transaction {field_name} is invalid")
        if positive and value == 0:
            raise ValueError(f"budget transaction {field_name} is invalid")
        return value

    chain_id = uint_field(transaction["chainId"], "chainId")
    transaction_nonce = uint_field(transaction["nonce"], "nonce")
    value = uint_field(transaction["value"], "value")
    gas_price = uint_field(transaction["gasPrice"], "gasPrice")
    gas = uint_field(transaction["gas"], "gas", positive=True)
    if chain_id != binding.chain_id or transaction_nonce != nonce or value != 0:
        raise ValueError("budget prepared transaction scope is invalid")
    try:
        transaction_from = canonicalize_evm_address(transaction["from"])
        transaction_to = canonicalize_evm_address(transaction["to"])
    except (TypeError, ValueError) as exc:
        raise ValueError("budget prepared transaction address is invalid") from exc
    if transaction_from != canonical_relayer or transaction_to != binding.executor_contract:
        raise ValueError("budget prepared transaction scope is invalid")
    data = transaction["data"]
    if not isinstance(data, str) or not data.startswith("0x") or len(data[2:]) % 2:
        raise ValueError("budget prepared transaction calldata is invalid")
    try:
        transaction_data = bytes.fromhex(data[2:])
    except ValueError as exc:
        raise ValueError("budget prepared transaction calldata is invalid") from exc

    try:
        decoded = rlp.decode(raw, Transaction)
        if int(decoded.v) < 35 or (int(decoded.v) - 35) // 2 != chain_id:
            raise ValueError("budget signed transaction chain id is invalid")
        if (
            int(decoded.nonce),
            int(decoded.gasPrice),
            int(decoded.gas),
            int(decoded.value),
            decoded.to,
            decoded.data,
        ) != (
            nonce,
            gas_price,
            gas,
            value,
            bytes.fromhex(transaction_to[2:]),
            transaction_data,
        ):
            raise ValueError("budget signed transaction differs from projection")
        recovered = canonicalize_evm_address(Account.recover_transaction(raw))
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("budget signed transaction"):
            raise
        raise ValueError("budget signed transaction is invalid") from exc
    if recovered != canonical_relayer:
        raise ValueError("budget signed transaction relayer does not match backend")

    PurchaseExecution, hash_execution, verify_execution_signature, encode_execute_calldata_hex = (
        _execution_protocol()
    )
    execution_raw = attempt.get("execution")
    try:
        execution = PurchaseExecution.from_json(execution_raw)
    except Exception as exc:
        raise ValueError("budget execution permit is invalid") from exc
    try:
        quote_hash = canonical_bytes32(row.get("quote_hash"), field_name="quote_hash")
        amount_atomic = row.get("amount_atomic")
        if not isinstance(amount_atomic, str) or not amount_atomic.isdigit():
            raise ValueError("budget reservation amount is invalid")
        amount = int(amount_atomic, 10)
        expected_purchase = purchase_commitment(row.get("purchase_id"))
        expected_grant = binding.grant_hash
        if (
            execution.grant_hash != expected_grant
            or execution.purchase_id != expected_purchase
            or execution.quote_hash != quote_hash
            or execution.amount != amount
            or execution.deadline < binding.valid_after
            or execution.deadline > binding.valid_until
            or execution.deadline <= now_timestamp
        ):
            raise ValueError("budget execution permit does not match reservation")
        expected_execution_digest = "0x" + hash_execution(
            execution, binding.chain_id, binding.executor_contract
        ).hex()
        execution_digest = canonical_bytes32(
            attempt.get("execution_digest"), field_name="execution_digest"
        )
        if execution_digest != expected_execution_digest:
            raise ValueError("budget execution digest does not match permit")
        execution_signature = canonical_signature(attempt.get("execution_signature"))
        verify_execution_signature(
            execution,
            execution_signature,
            binding.execution_signer,
            binding.chain_id,
            binding.executor_contract,
        )
        expected_data = encode_execute_calldata_hex(
            binding.grant,
            binding.owner_signature,
            execution,
            execution_signature,
        )
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("budget execution"):
            raise
        raise ValueError("budget execution permit or signature is invalid") from exc
    if data.lower() != expected_data.lower():
        raise ValueError("budget transaction calldata does not match execution permit")

    normalized_transaction = dict(transaction)
    normalized_transaction.update(
        {
            "chainId": chain_id,
            "nonce": transaction_nonce,
            "from": canonical_relayer,
            "to": binding.executor_contract,
            "value": value,
            "data": "0x" + transaction_data.hex(),
            "gasPrice": gas_price,
            "gas": gas,
        }
    )
    prepared = dict(attempt)
    prepared.update(
        {
            "raw_transaction": "0x" + raw.hex(),
            "tx_hash": tx_hash,
            "relayer": canonical_relayer,
            "nonce": nonce,
            "transaction": normalized_transaction,
            "execution": execution.to_json(),
            "execution_digest": expected_execution_digest,
            "execution_signature": execution_signature,
        }
    )
    return prepared
