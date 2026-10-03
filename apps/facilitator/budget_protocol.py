"""Wire protocol for the user-signed budget executor.

This module is deliberately limited to deterministic value validation and
encoding.  It does not broadcast transactions, hold keys, or decide whether a
Core purchase is authorized.  The two signatures in this protocol are:

* the owner's EIP-712 signature over :class:`SpendGrant`; and
* the execution signer's EIP-712 signature over :class:`PurchaseExecution`.

``hash_grant`` and ``hash_execution`` return the full EIP-712 digest as raw
32-byte values.  ``encode_execute_calldata`` returns the exact calldata for
``execute(SpendGrant,bytes,PurchaseExecution,bytes)``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

from eth_abi import encode as abi_encode
from eth_hash.auto import keccak
from eth_keys import keys


UINT256_MAX = 2**256 - 1
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
SECP256K1_HALF_N = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0

EIP712_DOMAIN_NAME = "Agentonomy Budget Executor"
EIP712_DOMAIN_VERSION = "1"
EIP712_DOMAIN_TYPE = (
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
SPEND_GRANT_TYPE = (
    "SpendGrant(bytes32 grantId,address owner,bytes32 agentScope,address token,address payee,"
    "uint256 maxPerPayment,uint256 maxTotal,uint256 validAfter,uint256 validUntil,address executionSigner)"
)
PURCHASE_EXECUTION_TYPE = (
    "PurchaseExecution(bytes32 grantHash,bytes32 purchaseId,bytes32 quoteHash,uint256 amount,uint256 deadline)"
)
EXECUTE_FUNCTION_TYPE = (
    "execute((bytes32,address,bytes32,address,address,uint256,uint256,uint256,uint256,address),bytes,"
    "(bytes32,bytes32,bytes32,uint256,uint256),bytes)"
)
EXECUTE_SELECTOR = keccak(EXECUTE_FUNCTION_TYPE.encode())[:4]

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
_UINT_STRING = re.compile(r"^(?:0|[1-9][0-9]*)$")

_GRANT_JSON_FIELDS = (
    "grantId",
    "owner",
    "agentScope",
    "token",
    "payee",
    "maxPerPayment",
    "maxTotal",
    "validAfter",
    "validUntil",
    "executionSigner",
)
_EXECUTION_JSON_FIELDS = (
    "grantHash",
    "purchaseId",
    "quoteHash",
    "amount",
    "deadline",
)


class BudgetProtocolError(ValueError):
    """Base error for malformed budget protocol values."""


class InvalidSignatureError(BudgetProtocolError):
    """A signature is malformed, non-canonical, or recovers unexpectedly."""


def _error(field: str, reason: str = "is invalid") -> BudgetProtocolError:
    return BudgetProtocolError(f"{field} {reason}")


def _bytes_from_hex(value: object, *, field: str, length: int | None = None) -> bytes:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str) and value.startswith("0x"):
        if len(value[2:]) % 2:
            raise _error(field)
        try:
            raw = bytes.fromhex(value[2:])
        except ValueError as exc:
            raise _error(field) from exc
    else:
        raise _error(field)
    if length is not None and len(raw) != length:
        raise _error(field)
    return raw


def _bytes32(value: object, *, field: str, nonzero: bool = True) -> str:
    if isinstance(value, str):
        if _HASH.fullmatch(value) is None:
            raise _error(field)
        raw = bytes.fromhex(value[2:])
    elif isinstance(value, bytes) and len(value) == 32:
        raw = value
    else:
        raise _error(field)
    if nonzero and raw == bytes(32):
        raise _error(field, "must be nonzero")
    return "0x" + raw.hex()


def _address(value: object, *, field: str, nonzero: bool = True) -> str:
    if not isinstance(value, str) or _ADDRESS.fullmatch(value) is None:
        raise _error(field)
    raw = bytes.fromhex(value[2:])
    if nonzero and raw == bytes(20):
        raise _error(field, "must be nonzero")
    return "0x" + raw.hex()


def _uint(value: object, *, field: str, positive: bool = False) -> int:
    if type(value) is not int or value < 0 or value > UINT256_MAX:
        raise _error(field, "must be uint256")
    if positive and value == 0:
        raise _error(field, "must be positive")
    return value


def _json_uint(value: object, *, field: str, positive: bool = False) -> int:
    if not isinstance(value, str) or _UINT_STRING.fullmatch(value) is None:
        raise _error(field, "must be a canonical unsigned decimal string")
    try:
        parsed = int(value, 10)
    except ValueError as exc:  # pragma: no cover - regex makes this unreachable
        raise _error(field) from exc
    return _uint(parsed, field=field, positive=positive)


def _require_object(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(field, "must be an object")
    return value


def _require_exact_fields(value: Mapping[str, Any], fields: tuple[str, ...], *, field: str) -> None:
    if set(value) != set(fields):
        raise _error(field, "has unexpected or missing fields")


def _domain(chain_id: object, executor: object) -> tuple[int, str]:
    return _uint(chain_id, field="chain_id"), _address(executor, field="executor")


@dataclass(frozen=True, slots=True)
class SpendGrant:
    """Immutable grant signed by the wallet owner.

    Attributes use Python ``snake_case``.  JSON uses the exact Solidity field
    names and encodes every uint256 as a canonical unsigned decimal string;
    use :meth:`from_json` and :meth:`to_json` at that boundary.
    """

    grant_id: str
    owner: str
    agent_scope: str
    token: str
    payee: str
    max_per_payment: int
    max_total: int
    valid_after: int
    valid_until: int
    execution_signer: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "grant_id", _bytes32(self.grant_id, field="grant_id"))
        object.__setattr__(self, "owner", _address(self.owner, field="owner"))
        object.__setattr__(self, "agent_scope", _bytes32(self.agent_scope, field="agent_scope"))
        object.__setattr__(self, "token", _address(self.token, field="token"))
        object.__setattr__(self, "payee", _address(self.payee, field="payee"))
        object.__setattr__(self, "execution_signer", _address(self.execution_signer, field="execution_signer"))
        if self.owner == self.payee:
            raise _error("owner", "must differ from payee")
        object.__setattr__(
            self,
            "max_per_payment",
            _uint(self.max_per_payment, field="max_per_payment", positive=True),
        )
        object.__setattr__(
            self,
            "max_total",
            _uint(self.max_total, field="max_total", positive=True),
        )
        if self.max_per_payment > self.max_total:
            raise _error("max_per_payment", "must not exceed max_total")
        object.__setattr__(self, "valid_after", _uint(self.valid_after, field="valid_after"))
        object.__setattr__(self, "valid_until", _uint(self.valid_until, field="valid_until"))
        if self.valid_after >= self.valid_until:
            raise _error("valid_after", "must be less than valid_until")

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "SpendGrant":
        raw = _require_object(value, field="grant")
        _require_exact_fields(raw, _GRANT_JSON_FIELDS, field="grant")
        return cls(
            grant_id=_bytes32(raw["grantId"], field="grantId"),
            owner=_address(raw["owner"], field="owner"),
            agent_scope=_bytes32(raw["agentScope"], field="agentScope"),
            token=_address(raw["token"], field="token"),
            payee=_address(raw["payee"], field="payee"),
            max_per_payment=_json_uint(raw["maxPerPayment"], field="maxPerPayment", positive=True),
            max_total=_json_uint(raw["maxTotal"], field="maxTotal", positive=True),
            valid_after=_json_uint(raw["validAfter"], field="validAfter"),
            valid_until=_json_uint(raw["validUntil"], field="validUntil"),
            execution_signer=_address(raw["executionSigner"], field="executionSigner"),
        )

    from_mapping = from_json

    def to_json(self) -> dict[str, str]:
        return {
            "grantId": self.grant_id,
            "owner": self.owner,
            "agentScope": self.agent_scope,
            "token": self.token,
            "payee": self.payee,
            "maxPerPayment": str(self.max_per_payment),
            "maxTotal": str(self.max_total),
            "validAfter": str(self.valid_after),
            "validUntil": str(self.valid_until),
            "executionSigner": self.execution_signer,
        }

    def typed_data(self, chain_id: int, executor: str) -> dict[str, Any]:
        chain_id, executor = _domain(chain_id, executor)
        return {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "SpendGrant": [
                    {"name": "grantId", "type": "bytes32"},
                    {"name": "owner", "type": "address"},
                    {"name": "agentScope", "type": "bytes32"},
                    {"name": "token", "type": "address"},
                    {"name": "payee", "type": "address"},
                    {"name": "maxPerPayment", "type": "uint256"},
                    {"name": "maxTotal", "type": "uint256"},
                    {"name": "validAfter", "type": "uint256"},
                    {"name": "validUntil", "type": "uint256"},
                    {"name": "executionSigner", "type": "address"},
                ],
            },
            "primaryType": "SpendGrant",
            "domain": {
                "name": EIP712_DOMAIN_NAME,
                "version": EIP712_DOMAIN_VERSION,
                "chainId": chain_id,
                "verifyingContract": executor,
            },
            "message": {
                "grantId": self.grant_id,
                "owner": self.owner,
                "agentScope": self.agent_scope,
                "token": self.token,
                "payee": self.payee,
                "maxPerPayment": self.max_per_payment,
                "maxTotal": self.max_total,
                "validAfter": self.valid_after,
                "validUntil": self.valid_until,
                "executionSigner": self.execution_signer,
            },
        }


@dataclass(frozen=True, slots=True)
class PurchaseExecution:
    """Immutable per-purchase execution permit signed by executionSigner."""

    grant_hash: str
    purchase_id: str
    quote_hash: str
    amount: int
    deadline: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "grant_hash", _bytes32(self.grant_hash, field="grant_hash"))
        object.__setattr__(self, "purchase_id", _bytes32(self.purchase_id, field="purchase_id"))
        object.__setattr__(self, "quote_hash", _bytes32(self.quote_hash, field="quote_hash"))
        object.__setattr__(self, "amount", _uint(self.amount, field="amount", positive=True))
        object.__setattr__(self, "deadline", _uint(self.deadline, field="deadline"))

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "PurchaseExecution":
        raw = _require_object(value, field="execution")
        _require_exact_fields(raw, _EXECUTION_JSON_FIELDS, field="execution")
        return cls(
            grant_hash=_bytes32(raw["grantHash"], field="grantHash"),
            purchase_id=_bytes32(raw["purchaseId"], field="purchaseId"),
            quote_hash=_bytes32(raw["quoteHash"], field="quoteHash"),
            amount=_json_uint(raw["amount"], field="amount", positive=True),
            deadline=_json_uint(raw["deadline"], field="deadline"),
        )

    from_mapping = from_json

    def to_json(self) -> dict[str, str]:
        return {
            "grantHash": self.grant_hash,
            "purchaseId": self.purchase_id,
            "quoteHash": self.quote_hash,
            "amount": str(self.amount),
            "deadline": str(self.deadline),
        }

    def typed_data(self, chain_id: int, executor: str) -> dict[str, Any]:
        chain_id, executor = _domain(chain_id, executor)
        return {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "PurchaseExecution": [
                    {"name": "grantHash", "type": "bytes32"},
                    {"name": "purchaseId", "type": "bytes32"},
                    {"name": "quoteHash", "type": "bytes32"},
                    {"name": "amount", "type": "uint256"},
                    {"name": "deadline", "type": "uint256"},
                ],
            },
            "primaryType": "PurchaseExecution",
            "domain": {
                "name": EIP712_DOMAIN_NAME,
                "version": EIP712_DOMAIN_VERSION,
                "chainId": chain_id,
                "verifyingContract": executor,
            },
            "message": {
                "grantHash": self.grant_hash,
                "purchaseId": self.purchase_id,
                "quoteHash": self.quote_hash,
                "amount": self.amount,
                "deadline": self.deadline,
            },
        }


def _grant(value: SpendGrant | Mapping[str, Any]) -> SpendGrant:
    if isinstance(value, SpendGrant):
        return value
    if isinstance(value, Mapping):
        # Python callers may provide either snake_case attrs or JSON wire keys.
        if set(value) == set(_GRANT_JSON_FIELDS):
            return SpendGrant.from_json(value)
        try:
            return SpendGrant(**value)
        except (TypeError, ValueError) as exc:
            raise _error("grant") from exc
    raise _error("grant", "must be SpendGrant")


def _execution(value: PurchaseExecution | Mapping[str, Any]) -> PurchaseExecution:
    if isinstance(value, PurchaseExecution):
        return value
    if isinstance(value, Mapping):
        if set(value) == set(_EXECUTION_JSON_FIELDS):
            return PurchaseExecution.from_json(value)
        try:
            return PurchaseExecution(**value)
        except (TypeError, ValueError) as exc:
            raise _error("execution") from exc
    raise _error("execution", "must be PurchaseExecution")


def _domain_separator(chain_id: int, executor: str) -> bytes:
    return keccak(
        abi_encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                keccak(EIP712_DOMAIN_TYPE.encode()),
                keccak(EIP712_DOMAIN_NAME.encode()),
                keccak(EIP712_DOMAIN_VERSION.encode()),
                chain_id,
                executor,
            ],
        )
    )


def _eip712_digest(domain_separator: bytes, struct_hash: bytes) -> bytes:
    return keccak(b"\x19\x01" + domain_separator + struct_hash)


def hash_grant(grant: SpendGrant | Mapping[str, Any], chain_id: int, executor: str) -> bytes:
    """Return the full EIP-712 digest of ``grant`` for this domain."""

    value = _grant(grant)
    chain_id, executor = _domain(chain_id, executor)
    struct_hash = keccak(
        abi_encode(
            [
                "bytes32",
                "bytes32",
                "address",
                "bytes32",
                "address",
                "address",
                "uint256",
                "uint256",
                "uint256",
                "uint256",
                "address",
            ],
            [
                keccak(SPEND_GRANT_TYPE.encode()),
                bytes.fromhex(value.grant_id[2:]),
                value.owner,
                bytes.fromhex(value.agent_scope[2:]),
                value.token,
                value.payee,
                value.max_per_payment,
                value.max_total,
                value.valid_after,
                value.valid_until,
                value.execution_signer,
            ],
        )
    )
    return _eip712_digest(_domain_separator(chain_id, executor), struct_hash)


def hash_execution(
    execution: PurchaseExecution | Mapping[str, Any], chain_id: int, executor: str
) -> bytes:
    """Return the full EIP-712 digest of ``execution`` for this domain."""

    value = _execution(execution)
    chain_id, executor = _domain(chain_id, executor)
    struct_hash = keccak(
        abi_encode(
            ["bytes32", "bytes32", "bytes32", "bytes32", "uint256", "uint256"],
            [
                keccak(PURCHASE_EXECUTION_TYPE.encode()),
                bytes.fromhex(value.grant_hash[2:]),
                bytes.fromhex(value.purchase_id[2:]),
                bytes.fromhex(value.quote_hash[2:]),
                value.amount,
                value.deadline,
            ],
        )
    )
    return _eip712_digest(_domain_separator(chain_id, executor), struct_hash)


grant_digest = hash_grant
execution_digest = hash_execution


def _signature(value: object) -> bytes:
    signature = _bytes_from_hex(value, field="signature", length=65)
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:64], "big")
    v = signature[64]
    if v not in (27, 28):
        raise InvalidSignatureError("signature recovery id must be 27 or 28")
    if r == 0 or r >= SECP256K1_N or s == 0 or s > SECP256K1_HALF_N:
        raise InvalidSignatureError("signature scalar is not canonical low-s")
    return signature


def recover_signer(digest: bytes | str, signature: bytes | str) -> str:
    """Recover a lower-case EVM address from a canonical 65-byte signature."""

    digest_bytes = _bytes_from_hex(digest, field="digest", length=32)
    signature_bytes = _signature(signature)
    r = int.from_bytes(signature_bytes[:32], "big")
    s = int.from_bytes(signature_bytes[32:64], "big")
    v = signature_bytes[64]
    try:
        public_key = keys.Signature(vrs=(v - 27, r, s)).recover_public_key_from_msg_hash(digest_bytes)
    except Exception as exc:  # eth-keys exposes several version-specific error classes
        raise InvalidSignatureError("signature cannot be recovered") from exc
    address = public_key.to_checksum_address().lower()
    if address == "0x" + "00" * 20:
        raise InvalidSignatureError("recovered signer is zero")
    return address


def recover_grant_signer(
    grant: SpendGrant | Mapping[str, Any], signature: bytes | str, chain_id: int, executor: str
) -> str:
    return recover_signer(hash_grant(grant, chain_id, executor), signature)


def recover_execution_signer(
    execution: PurchaseExecution | Mapping[str, Any], signature: bytes | str, chain_id: int, executor: str
) -> str:
    return recover_signer(hash_execution(execution, chain_id, executor), signature)


def verify_grant_signature(
    grant: SpendGrant | Mapping[str, Any], signature: bytes | str, chain_id: int, executor: str
) -> str:
    """Recover and require the owner's signature, returning the signer."""

    value = _grant(grant)
    recovered = recover_grant_signer(value, signature, chain_id, executor)
    if recovered != value.owner:
        raise InvalidSignatureError("grant signature does not recover owner")
    return recovered


def verify_execution_signature(
    execution: PurchaseExecution | Mapping[str, Any],
    signature: bytes | str,
    expected_signer: str,
    chain_id: int,
    executor: str,
) -> str:
    """Recover and require the declared execution signer."""

    recovered = recover_execution_signer(execution, signature, chain_id, executor)
    if recovered != _address(expected_signer, field="expected_signer"):
        raise InvalidSignatureError("execution signature does not recover execution signer")
    return recovered


def _private_key(value: object) -> keys.PrivateKey:
    raw = _bytes_from_hex(value, field="private_key", length=32)
    try:
        return keys.PrivateKey(raw)
    except Exception as exc:
        raise BudgetProtocolError("private_key is invalid") from exc


def _sign_digest(digest: bytes, private_key: object) -> bytes:
    try:
        signed = _private_key(private_key).sign_msg_hash(digest)
    except BudgetProtocolError:
        raise
    except Exception as exc:
        raise BudgetProtocolError("private_key cannot sign digest") from exc
    if signed.s > SECP256K1_HALF_N:
        raise BudgetProtocolError("signer returned a high-s signature")
    return signed.r.to_bytes(32, "big") + signed.s.to_bytes(32, "big") + bytes([signed.v + 27])


def sign_grant(
    grant: SpendGrant | Mapping[str, Any], private_key: bytes | str, chain_id: int, executor: str
) -> bytes:
    return _sign_digest(hash_grant(grant, chain_id, executor), private_key)


def sign_execution(
    execution: PurchaseExecution | Mapping[str, Any], private_key: bytes | str, chain_id: int, executor: str
) -> bytes:
    return _sign_digest(hash_execution(execution, chain_id, executor), private_key)


_GRANT_ABI_TYPE = "(bytes32,address,bytes32,address,address,uint256,uint256,uint256,uint256,address)"
_EXECUTION_ABI_TYPE = "(bytes32,bytes32,bytes32,uint256,uint256)"


def encode_execute_calldata(
    grant: SpendGrant | Mapping[str, Any],
    owner_signature: bytes | str,
    execution: PurchaseExecution | Mapping[str, Any],
    execution_signature: bytes | str,
) -> bytes:
    """ABI encode the exact Solidity ``execute`` call, including selector."""

    grant_value = _grant(grant)
    execution_value = _execution(execution)
    owner_sig = _signature(owner_signature)
    execution_sig = _signature(execution_signature)
    encoded = abi_encode(
        [_GRANT_ABI_TYPE, "bytes", _EXECUTION_ABI_TYPE, "bytes"],
        [
            [
                bytes.fromhex(grant_value.grant_id[2:]),
                grant_value.owner,
                bytes.fromhex(grant_value.agent_scope[2:]),
                grant_value.token,
                grant_value.payee,
                grant_value.max_per_payment,
                grant_value.max_total,
                grant_value.valid_after,
                grant_value.valid_until,
                grant_value.execution_signer,
            ],
            owner_sig,
            [
                bytes.fromhex(execution_value.grant_hash[2:]),
                bytes.fromhex(execution_value.purchase_id[2:]),
                bytes.fromhex(execution_value.quote_hash[2:]),
                execution_value.amount,
                execution_value.deadline,
            ],
            execution_sig,
        ],
    )
    return EXECUTE_SELECTOR + encoded


def encode_execute_calldata_hex(
    grant: SpendGrant | Mapping[str, Any],
    owner_signature: bytes | str,
    execution: PurchaseExecution | Mapping[str, Any],
    execution_signature: bytes | str,
) -> str:
    return "0x" + encode_execute_calldata(grant, owner_signature, execution, execution_signature).hex()


__all__ = [
    "BudgetProtocolError",
    "InvalidSignatureError",
    "SpendGrant",
    "PurchaseExecution",
    "UINT256_MAX",
    "EIP712_DOMAIN_NAME",
    "EIP712_DOMAIN_VERSION",
    "EIP712_DOMAIN_TYPE",
    "SPEND_GRANT_TYPE",
    "PURCHASE_EXECUTION_TYPE",
    "EXECUTE_FUNCTION_TYPE",
    "EXECUTE_SELECTOR",
    "hash_grant",
    "hash_execution",
    "grant_digest",
    "execution_digest",
    "recover_signer",
    "recover_grant_signer",
    "recover_execution_signer",
    "verify_grant_signature",
    "verify_execution_signature",
    "sign_grant",
    "sign_execution",
    "encode_execute_calldata",
    "encode_execute_calldata_hex",
]
