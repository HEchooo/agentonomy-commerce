"""Small, dependency-light KMS and legacy EVM transaction adapter.

This module deliberately contains no AWS SDK imports.  A caller supplies an
object with the narrow ``get_public_key`` and ``sign`` methods exposed by KMS,
which keeps credential loading and transport configuration outside the signing
primitive.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re
from typing import Any

import rlp
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import load_der_public_key
from eth_keys import keys
from eth_utils import keccak


KMS_KEY_SPEC = "ECC_SECG_P256K1"
KMS_KEY_USAGE = "SIGN_VERIFY"
KMS_SIGNING_ALGORITHM = "ECDSA_SHA_256"
KMS_MESSAGE_TYPE = "DIGEST"

SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
SECP256K1_HALF_N = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0
UINT256_MAX = (1 << 256) - 1


class KmsConfigurationError(ValueError):
    """Raised when the adapter's static configuration is unsafe."""


class KmsSigningError(RuntimeError):
    """Raised when KMS cannot provide a verified secp256k1 signature."""


# Keep the uppercase spellings available to callers that already use the
# copied facilitator adapter's exception names.
KMSConfigurationError = KmsConfigurationError
KMSSigningError = KmsSigningError


_KEY_ARN_RE = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):kms:[a-z0-9-]+:[0-9]{12}:key/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_HEX_RE = re.compile(r"^[0-9a-fA-F]*$")


def _validate_key_arn(value: object) -> str:
    if not isinstance(value, str) or _KEY_ARN_RE.fullmatch(value) is None:
        raise KmsConfigurationError("key_arn must be a specific KMS key ARN")
    return value


def _normalise_address(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 42 or value[:2].lower() != "0x":
        raise KmsConfigurationError(f"{field} must be a nonzero 20-byte address")
    body = value[2:]
    if _HEX_RE.fullmatch(body) is None:
        raise KmsConfigurationError(f"{field} must be a nonzero 20-byte address")
    try:
        raw = bytes.fromhex(body)
    except (TypeError, ValueError):
        raise KmsConfigurationError(f"{field} must be a nonzero 20-byte address") from None
    if len(raw) != 20 or raw == bytes(20):
        raise KmsConfigurationError(f"{field} must be a nonzero 20-byte address")
    return "0x" + raw.hex()


def _public_key_bytes(public_key: ec.EllipticCurvePublicKey) -> bytes:
    numbers = public_key.public_numbers()
    try:
        x = numbers.x.to_bytes(32, "big")
        y = numbers.y.to_bytes(32, "big")
    except (OverflowError, ValueError):
        raise KmsSigningError("KMS public key is invalid") from None
    return b"\x04" + x + y


def _ethereum_address(public_key_bytes: bytes) -> str:
    if len(public_key_bytes) != 65 or public_key_bytes[0] != 4:
        raise KmsSigningError("KMS public key is invalid")
    address = keccak(public_key_bytes[1:])[-20:]
    if address == bytes(20):
        raise KmsSigningError("KMS public key address is invalid")
    return "0x" + address.hex()


def _as_bytes(value: object) -> bytes | None:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        return None
    try:
        return bytes(value)
    except (TypeError, ValueError):
        return None


class KmsDigestSigner:
    """Verify and use one AWS KMS secp256k1 signing key.

    The object never loads credentials or stores private key material.  The
    supplied client is expected to be an already configured KMS client or a
    narrow test double implementing the two methods used here.
    """

    def __init__(self, client: Any, key_arn: str, expected_address: str) -> None:
        self._key_arn = _validate_key_arn(key_arn)
        self._expected_address = _normalise_address(expected_address, field="expected_address")
        self._client = client
        self._pinned_public_key: bytes | None = None

    @property
    def key_arn(self) -> str:
        """Return the configured key ARN for internal callers.

        ``repr`` intentionally redacts this value; callers should avoid
        logging this property because it identifies the signing key.
        """

        return self._key_arn

    @property
    def expected_address(self) -> str:
        return self._expected_address

    def __repr__(self) -> str:
        return "KmsDigestSigner(key_arn='<redacted>', expected_address={!r})".format(
            self._expected_address
        )

    def _load_public_key(self) -> tuple[bytes, str]:
        try:
            response = self._client.get_public_key(KeyId=self._key_arn)
        except Exception:
            raise KmsSigningError("KMS public-key lookup failed") from None
        if not isinstance(response, Mapping):
            raise KmsSigningError("KMS public-key response is invalid")
        try:
            key_spec = response.get("KeySpec")
            key_usage = response.get("KeyUsage")
            der_value = response.get("PublicKey")
        except Exception:
            raise KmsSigningError("KMS public-key response is invalid") from None
        if key_spec != KMS_KEY_SPEC or key_usage != KMS_KEY_USAGE:
            raise KmsSigningError("KMS key metadata is invalid")
        der = _as_bytes(der_value)
        if der is None:
            raise KmsSigningError("KMS public-key response is invalid")
        try:
            loaded = load_der_public_key(der)
        except Exception:
            raise KmsSigningError("KMS public key is invalid") from None
        if not isinstance(loaded, ec.EllipticCurvePublicKey):
            raise KmsSigningError("KMS public key is not secp256k1")
        if loaded.curve.name != "secp256k1":
            raise KmsSigningError("KMS public key is not secp256k1")
        public_key_bytes = _public_key_bytes(loaded)
        return public_key_bytes, _ethereum_address(public_key_bytes)

    def validate(self) -> str:
        """Validate and pin the configured KMS public key.

        Returns the derived Ethereum address in normalized lowercase form.
        """

        public_key_bytes, public_address = self._load_public_key()
        if public_address != self._expected_address:
            raise KmsSigningError("KMS signer address mismatch")
        self._pinned_public_key = public_key_bytes
        return public_address

    def sign_digest(self, digest: bytes) -> bytes:
        """Sign one 32-byte digest and return compact ``r || s || v`` bytes."""

        digest_bytes = _as_bytes(digest)
        if digest_bytes is None or len(digest_bytes) != 32:
            raise ValueError("digest must be exactly 32 bytes")

        public_key_bytes, public_address = self._load_public_key()
        if public_address != self._expected_address:
            raise KmsSigningError("KMS signer address mismatch")
        if self._pinned_public_key is not None and public_key_bytes != self._pinned_public_key:
            raise KmsSigningError("KMS public key changed")
        if self._pinned_public_key is None:
            self._pinned_public_key = public_key_bytes

        try:
            response = self._client.sign(
                KeyId=self._key_arn,
                Message=digest_bytes,
                MessageType=KMS_MESSAGE_TYPE,
                SigningAlgorithm=KMS_SIGNING_ALGORITHM,
            )
        except Exception:
            raise KmsSigningError("KMS signing request failed") from None
        if not isinstance(response, Mapping):
            raise KmsSigningError("KMS signature response is invalid")
        try:
            signature_value = response.get("Signature")
        except Exception:
            raise KmsSigningError("KMS signature response is invalid") from None
        der_signature = _as_bytes(signature_value)
        if der_signature is None:
            raise KmsSigningError("KMS signature response is invalid")
        try:
            r, s = decode_dss_signature(der_signature)
        except Exception:
            raise KmsSigningError("KMS signature encoding is invalid") from None
        if r <= 0 or r >= SECP256K1_N or s <= 0 or s >= SECP256K1_N:
            raise KmsSigningError("KMS signature scalar is invalid")
        if s > SECP256K1_HALF_N:
            s = SECP256K1_N - s
        parity = self._recover_parity(digest_bytes, r, s, public_key_bytes)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes((27 + parity,))

    @staticmethod
    def _recover_parity(digest: bytes, r: int, s: int, public_key_bytes: bytes) -> int:
        for parity in (0, 1):
            try:
                recovered = keys.Signature(vrs=(parity, r, s)).recover_public_key_from_msg_hash(digest)
            except Exception:
                continue
            try:
                recovered_bytes = recovered.to_bytes()
            except Exception:
                continue
            if recovered_bytes == public_key_bytes[1:]:
                return parity
        raise KmsSigningError("KMS signature does not match signer")


def _uint256(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0 or value > UINT256_MAX:
        raise ValueError(f"legacy transaction {field} is invalid")
    return value


def _rlp_uint(value: int) -> bytes:
    if value == 0:
        return b""
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def _decode_data(value: object) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("legacy transaction data is invalid")
    body = value[2:]
    if len(body) % 2 or _HEX_RE.fullmatch(body) is None:
        raise ValueError("legacy transaction data is invalid")
    try:
        return bytes.fromhex(body)
    except (TypeError, ValueError):
        raise ValueError("legacy transaction data is invalid") from None


def _decode_to(value: object) -> bytes:
    if value is None:
        return b""
    if isinstance(value, str):
        if value in ("", "0x", "0X"):
            return b""
        normalized = _normalise_address(value, field="to")
        return bytes.fromhex(normalized[2:])
    raw = _as_bytes(value)
    if raw == b"":
        return b""
    if raw is None or len(raw) != 20 or raw == bytes(20):
        raise ValueError("legacy transaction to is invalid")
    return raw


def _recover_address(digest: bytes, signature: bytes) -> str:
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:64], "big")
    parity = signature[64] - 27
    try:
        recovered = keys.Signature(vrs=(parity, r, s)).recover_public_key_from_msg_hash(digest)
        return recovered.to_checksum_address().lower()
    except Exception:
        raise KmsSigningError("legacy signature does not match signer") from None


def sign_legacy_transaction(
    transaction: Mapping[str, object],
    sign_digest: Callable[[bytes], bytes],
    *,
    expected_address: str,
    chain_id: int,
) -> bytes:
    """Sign a conventional EIP-155 legacy transaction.

    The input is validated as a fixed, untyped transaction shape and is never
    mutated.  Chain and spending policy checks belong to the caller/worker.
    """

    expected = _normalise_address(expected_address, field="expected_address")
    selected_chain_id = _uint256(chain_id, field="chain_id")
    if not isinstance(transaction, Mapping):
        raise ValueError("legacy transaction fields are invalid")

    required = {"chainId", "nonce", "to", "value", "data", "gasPrice", "gas"}
    allowed = required | {"from"}
    try:
        actual_fields = set(transaction.keys())
    except Exception:
        raise ValueError("legacy transaction fields are invalid") from None
    if actual_fields not in (required, allowed):
        raise ValueError("legacy transaction fields are invalid")

    values: dict[str, int] = {
        field: _uint256(transaction[field], field=field)
        for field in ("chainId", "nonce", "value", "gasPrice", "gas")
    }
    if values["chainId"] != selected_chain_id:
        raise ValueError("legacy transaction chain id mismatch")

    if "from" in transaction:
        sender = _normalise_address(transaction["from"], field="from")
        if sender != expected:
            raise ValueError("legacy transaction sender mismatch")

    to_bytes = _decode_to(transaction["to"])
    data_bytes = _decode_data(transaction["data"])
    unsigned_payload = rlp.encode(
        [
            _rlp_uint(values["nonce"]),
            _rlp_uint(values["gasPrice"]),
            _rlp_uint(values["gas"]),
            to_bytes,
            _rlp_uint(values["value"]),
            data_bytes,
            _rlp_uint(values["chainId"]),
            b"",
            b"",
        ]
    )
    digest = keccak(unsigned_payload)

    try:
        signature_value = sign_digest(digest)
    except Exception:
        raise KmsSigningError("legacy transaction signing failed") from None
    signature = _as_bytes(signature_value)
    if signature is None or len(signature) != 65:
        raise ValueError("legacy signature must be 65 bytes")
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:64], "big")
    v = signature[64]
    if r <= 0 or r >= SECP256K1_N or s <= 0 or s >= SECP256K1_N:
        raise ValueError("legacy signature scalar is invalid")
    if s > SECP256K1_HALF_N:
        raise ValueError("legacy signature must use low s")
    if v not in (27, 28):
        raise ValueError("legacy signature recovery id is invalid")
    recovered = _recover_address(digest, signature)
    if recovered != expected:
        raise KmsSigningError("legacy signature does not match signer")

    signed_v = selected_chain_id * 2 + 35 + (v - 27)
    return rlp.encode(
        [
            _rlp_uint(values["nonce"]),
            _rlp_uint(values["gasPrice"]),
            _rlp_uint(values["gas"]),
            to_bytes,
            _rlp_uint(values["value"]),
            data_bytes,
            _rlp_uint(signed_v),
            _rlp_uint(r),
            _rlp_uint(s),
        ]
    )
