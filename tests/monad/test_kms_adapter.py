from __future__ import annotations

import pytest
import rlp
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from eth_account import Account
from eth_keys import keys
from eth_utils import keccak

from agentonomy_commerce.kms_adapter import (
    KMS_KEY_SPEC,
    KMS_KEY_USAGE,
    KMS_MESSAGE_TYPE,
    KMS_SIGNING_ALGORITHM,
    KmsDigestSigner,
    sign_legacy_transaction,
)


KEY_ARN = "arn:aws:kms:us-east-1:123456789012:key/11111111-2222-3333-4444-555555555555"
PRIVATE_KEY_BYTES = bytes.fromhex("4f3edf983ac636a65a842ce7c78d9aa706d3b113bce036f9f11e6f3c5f2d2f9a")
WRONG_PRIVATE_KEY_BYTES = bytes.fromhex("6c875f9b2f23d0c64e1ccf13db1e8b8eacfc7ea4d5f1b61a3062f6d9e2c5a7d1")


def _address(private_key: bytes) -> str:
    return keys.PrivateKey(private_key).public_key.to_checksum_address().lower()


def _public_der(private_key: bytes) -> bytes:
    private = ec.derive_private_key(int.from_bytes(private_key, "big"), ec.SECP256K1())
    return private.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


class FakeKms:
    def __init__(self, private_key: bytes = PRIVATE_KEY_BYTES) -> None:
        self.private_key = private_key
        self.public_response: object = {
            "KeySpec": KMS_KEY_SPEC,
            "KeyUsage": KMS_KEY_USAGE,
            "PublicKey": _public_der(private_key),
        }
        self.signature: bytes | None = None
        self.sign_exception: Exception | None = None
        self.public_exception: Exception | None = None
        self.public_calls: list[dict[str, object]] = []
        self.sign_calls: list[dict[str, object]] = []
        self.high_s = False

    def get_public_key(self, **kwargs: object) -> object:
        self.public_calls.append(kwargs)
        if self.public_exception is not None:
            raise self.public_exception
        return self.public_response

    def sign(self, **kwargs: object) -> object:
        self.sign_calls.append(kwargs)
        if self.sign_exception is not None:
            raise self.sign_exception
        if self.signature is not None:
            return {"Signature": self.signature}
        digest = kwargs["Message"]
        assert isinstance(digest, bytes)
        signed = keys.PrivateKey(self.private_key).sign_msg_hash(digest)
        s = signed.s
        if self.high_s:
            s = _SECP256K1_N - s
        return {"Signature": encode_dss_signature(signed.r, s)}


_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_SECP256K1_HALF_N = _SECP256K1_N // 2


def _signer(fake: FakeKms | None = None) -> tuple[KmsDigestSigner, FakeKms, str]:
    kms = fake or FakeKms()
    expected = _address(kms.private_key)
    return KmsDigestSigner(kms, KEY_ARN, expected.upper()), kms, expected


def _transaction(expected: str, **changes: object) -> dict[str, object]:
    transaction: dict[str, object] = {
        "chainId": 10143,
        "nonce": 7,
        "to": "0x" + "22" * 20,
        "value": 1234,
        "data": "0x6001600055",
        "gasPrice": 2_000_000_000,
        "gas": 120_000,
        "from": expected,
    }
    transaction.update(changes)
    return transaction


def test_validate_derives_lowercase_address_and_redacts_key_reference() -> None:
    signer, kms, expected = _signer()

    assert signer.validate() == expected
    assert kms.public_calls == [{"KeyId": KEY_ARN}]
    assert KEY_ARN not in repr(signer)
    assert "11111111-2222-3333-4444-555555555555" not in str(signer)


@pytest.mark.parametrize(
    "key_arn",
    [
        "arn:aws:kms:us-east-1:123456789012:alias/my-key",
        "arn:aws:kms:us-east-1:123456789012:key/*",
        "arn:aws:kms::123456789012:key/11111111-2222-3333-4444-555555555555",
        "arn:aws:kms:us-east-1::key/11111111-2222-3333-4444-555555555555",
        "arn:aws:kms:us-east-1:123456789012:key/not-a-uuid",
        "arn:aws:kms:us-east-1:123456789012:key/11111111-2222-3333-4444-555555555555*",
    ],
)
def test_rejects_non_specific_key_arns(key_arn: str) -> None:
    with pytest.raises(ValueError):
        KmsDigestSigner(FakeKms(), key_arn, _address(PRIVATE_KEY_BYTES))


@pytest.mark.parametrize("expected", ["0x" + "00" * 20, "0x1234", "not-an-address", None])
def test_rejects_invalid_expected_address(expected: object) -> None:
    with pytest.raises(ValueError):
        KmsDigestSigner(FakeKms(), KEY_ARN, expected)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["KeySpec", "KeyUsage", "PublicKey"])
def test_validate_rejects_bad_public_key_response(field: str) -> None:
    signer, kms, _ = _signer()
    response = dict(kms.public_response)  # type: ignore[arg-type]
    if field == "KeySpec":
        response[field] = "RSA_2048"
    elif field == "KeyUsage":
        response[field] = "ENCRYPT_DECRYPT"
    else:
        response[field] = b"not-der"
    kms.public_response = response

    with pytest.raises(RuntimeError, match="public key|key metadata"):
        signer.validate()


def test_validate_rejects_non_secp256k1_curve() -> None:
    signer, kms, _ = _signer()
    public = ec.generate_private_key(ec.SECP256R1()).public_key()
    kms.public_response = {
        "KeySpec": KMS_KEY_SPEC,
        "KeyUsage": KMS_KEY_USAGE,
        "PublicKey": public.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo),
    }

    with pytest.raises(RuntimeError, match="secp256k1"):
        signer.validate()


def test_sign_digest_sends_digest_parameters_and_returns_recoverable_low_s() -> None:
    signer, kms, expected = _signer()
    digest = keccak(b"kms digest")

    signature = signer.sign_digest(digest)

    assert len(signature) == 65
    assert signature[64] in (27, 28)
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:64], "big")
    assert 0 < r < _SECP256K1_N
    assert 0 < s <= _SECP256K1_HALF_N
    recovered = keys.Signature(vrs=(signature[64] - 27, r, s)).recover_public_key_from_msg_hash(digest)
    assert recovered.to_checksum_address().lower() == expected
    assert kms.sign_calls[-1] == {
        "KeyId": KEY_ARN,
        "Message": digest,
        "MessageType": KMS_MESSAGE_TYPE,
        "SigningAlgorithm": KMS_SIGNING_ALGORITHM,
    }


def test_sign_digest_normalizes_high_s_before_recovery() -> None:
    signer, kms, expected = _signer()
    kms.high_s = True
    digest = keccak(b"high s")

    signature = signer.sign_digest(digest)
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:64], "big")

    assert s <= _SECP256K1_HALF_N
    assert keys.Signature(vrs=(signature[64] - 27, r, s)).recover_public_key_from_msg_hash(digest).to_checksum_address().lower() == expected


@pytest.mark.parametrize("digest", [b"", b"0" * 31, b"0" * 33, "0" * 32, None])
def test_sign_digest_requires_exact_32_bytes(digest: object) -> None:
    signer, _, _ = _signer()

    with pytest.raises(ValueError, match="32 bytes"):
        signer.sign_digest(digest)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "signature",
    [
        b"not-der",
        encode_dss_signature(0, 1),
        encode_dss_signature(1, 0),
        encode_dss_signature(_SECP256K1_N, 1),
        encode_dss_signature(1, _SECP256K1_N),
    ],
)
def test_sign_digest_rejects_malformed_der_or_scalars(signature: bytes) -> None:
    signer, kms, _ = _signer()
    kms.signature = signature

    with pytest.raises(RuntimeError, match="signature"):
        signer.sign_digest(keccak(b"bad signature"))


def test_sign_digest_rejects_wrong_key_signature() -> None:
    signer, kms, _ = _signer()
    wrong = keys.PrivateKey(WRONG_PRIVATE_KEY_BYTES).sign_msg_hash(keccak(b"wrong key"))
    kms.signature = encode_dss_signature(wrong.r, wrong.s)

    with pytest.raises(RuntimeError, match="signer|signature"):
        signer.sign_digest(keccak(b"wrong key"))


@pytest.mark.parametrize("operation", ["public", "sign"])
def test_provider_errors_are_redacted_and_have_no_cause(operation: str) -> None:
    signer, kms, _ = _signer()
    secret = "kms-provider-secret-arn"
    if operation == "public":
        kms.public_exception = RuntimeError(secret)
    else:
        kms.sign_exception = RuntimeError(secret)

    with pytest.raises(RuntimeError) as caught:
        signer.validate() if operation == "public" else signer.sign_digest(keccak(b"provider"))
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None


def test_legacy_transaction_matches_eth_account_and_preserves_input() -> None:
    signer, _, expected = _signer()
    transaction = _transaction(expected)
    original = dict(transaction)

    raw = sign_legacy_transaction(
        transaction,
        signer.sign_digest,
        expected_address=expected,
        chain_id=10143,
    )
    expected_raw = Account.sign_transaction(
        {key: value for key, value in transaction.items() if key != "from"},
        PRIVATE_KEY_BYTES,
    ).raw_transaction

    assert raw == bytes(expected_raw)
    assert transaction == original


@pytest.mark.parametrize("to", [None, "", "0x"])
def test_legacy_contract_creation_encodes_empty_to(to: object) -> None:
    signer, _, expected = _signer()
    transaction = _transaction(expected, to=to, data="0x6000")

    raw = sign_legacy_transaction(transaction, signer.sign_digest, expected_address=expected, chain_id=10143)
    fields = rlp.decode(raw)
    assert fields[3] == b""
    assert fields[5] == bytes.fromhex("6000")


@pytest.mark.parametrize(
    "changes",
    [
        {"type": 2},
        {"maxFeePerGas": 1},
        {"accessList": []},
        {"nonce": True},
        {"gas": 1.0},
        {"value": -1},
        {"gasPrice": 2**256},
        {"chainId": 1},
        {"from": "0x" + "33" * 20},
        {"to": "0x" + "00" * 20},
        {"to": "0x1234"},
        {"data": "6000"},
        {"data": "0x600"},
        {"data": "0xnot-hex"},
    ],
)
def test_legacy_transaction_rejects_unsafe_or_changed_fields(changes: dict[str, object]) -> None:
    signer, _, expected = _signer()
    transaction = _transaction(expected, **changes)

    with pytest.raises(ValueError):
        sign_legacy_transaction(transaction, signer.sign_digest, expected_address=expected, chain_id=10143)


def test_legacy_transaction_rejects_wrong_signer_and_high_s_callback() -> None:
    _, _, expected = _signer()
    transaction = _transaction(expected)
    wrong = keys.PrivateKey(WRONG_PRIVATE_KEY_BYTES)

    def wrong_signer(digest: bytes) -> bytes:
        signed = wrong.sign_msg_hash(digest)
        return signed.r.to_bytes(32, "big") + signed.s.to_bytes(32, "big") + bytes([27 + signed.v])

    with pytest.raises(RuntimeError, match="signer|signature"):
        sign_legacy_transaction(transaction, wrong_signer, expected_address=expected, chain_id=10143)

    def high_s_signer(digest: bytes) -> bytes:
        signed = keys.PrivateKey(PRIVATE_KEY_BYTES).sign_msg_hash(digest)
        return signed.r.to_bytes(32, "big") + (_SECP256K1_N - signed.s).to_bytes(32, "big") + bytes([27 + signed.v])

    with pytest.raises(ValueError, match="signature"):
        sign_legacy_transaction(transaction, high_s_signer, expected_address=expected, chain_id=10143)


def test_legacy_transaction_rejects_missing_or_extra_fields() -> None:
    signer, _, expected = _signer()
    transaction = _transaction(expected)

    for key in ("chainId", "nonce", "to", "value", "data", "gasPrice", "gas"):
        missing = dict(transaction)
        del missing[key]
        with pytest.raises(ValueError):
            sign_legacy_transaction(missing, signer.sign_digest, expected_address=expected, chain_id=10143)

    extra = dict(transaction)
    extra["blobVersionedHashes"] = []
    with pytest.raises(ValueError):
        sign_legacy_transaction(extra, signer.sign_digest, expected_address=expected, chain_id=10143)
