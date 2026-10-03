from __future__ import annotations

import json
from pathlib import Path

import pytest
from eth_account import Account

from budget_protocol import (
    PurchaseExecution,
    SpendGrant,
    BudgetProtocolError,
    encode_execute_calldata,
    hash_execution,
    hash_grant,
    recover_execution_signer,
    recover_grant_signer,
    sign_execution,
    sign_grant,
    verify_grant_signature,
)


FIXTURE = Path(__file__).with_name("fixtures") / "grant-vectors.json"


def _vector() -> dict[str, object]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert not any("private_key" in key for key in value)
    # Public deterministic test-only identities, generated without key files.
    return value | {"owner_private_key": bytes(range(32)), "execution_private_key": bytes(reversed(range(32)))}


def _models() -> tuple[dict[str, object], SpendGrant, PurchaseExecution]:
    vector = _vector()
    domain = vector["domain"]
    grant = SpendGrant.from_json(vector["grant"])
    execution = PurchaseExecution.from_json(vector["execution"])
    return domain, grant, execution


def test_shared_vector_matches_digest_signatures_and_exact_abi() -> None:
    vector = _vector()
    domain, grant, execution = _models()
    chain_id = domain["chain_id"]
    executor = domain["executor"]

    assert "private_key" not in vector
    assert hash_grant(grant, chain_id, executor).hex() == vector["grant_digest"][2:]
    assert hash_execution(execution, chain_id, executor).hex() == vector["execution_digest"][2:]
    assert sign_grant(grant, vector["owner_private_key"], chain_id, executor).hex() == vector[
        "owner_signature"
    ][2:]
    assert sign_execution(
        execution, vector["execution_private_key"], chain_id, executor
    ).hex() == vector["execution_signature"][2:]
    assert encode_execute_calldata(
        grant,
        vector["owner_signature"],
        execution,
        vector["execution_signature"],
    ).hex() == vector["execute_calldata"][2:]


def test_vector_signatures_recover_the_declared_signers() -> None:
    vector = _vector()
    domain, grant, execution = _models()

    assert recover_grant_signer(
        grant, vector["owner_signature"], domain["chain_id"], domain["executor"]
    ) == grant.owner
    assert recover_execution_signer(
        execution,
        vector["execution_signature"],
        domain["chain_id"],
        domain["executor"],
    ) == Account.from_key(vector["execution_private_key"]).address.lower()


def test_wrong_domain_cannot_recover_the_same_grant_signer() -> None:
    vector = _vector()
    grant = SpendGrant.from_json(vector["grant"])
    signature = vector["owner_signature"]

    assert recover_grant_signer(
        grant, signature, vector["domain"]["chain_id"] + 1, vector["domain"]["executor"]
    ) != grant.owner
    with pytest.raises(BudgetProtocolError):
        verify_grant_signature(
            grant, signature, vector["domain"]["chain_id"] + 1, vector["domain"]["executor"]
        )
    with pytest.raises(BudgetProtocolError):
        verify_grant_signature(
            grant, signature, vector["domain"]["chain_id"], "0x" + "98" * 20
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("grant_id", "0x" + "00" * 32),
        ("agent_scope", "0x" + "00" * 32),
        ("owner", "0x" + "00" * 20),
        ("payee", "0x" + "00" * 20),
        ("token", "0x" + "00" * 20),
        ("execution_signer", "0x" + "00" * 20),
        ("max_total", 1.0),
        ("max_per_payment", "1"),
    ],
)
def test_grant_rejects_zero_or_non_integer_wire_values(field: str, value: object) -> None:
    vector = _vector()
    grant = dict(vector["grant"])
    grant[field] = value
    with pytest.raises(BudgetProtocolError):
        SpendGrant.from_json(grant)


def test_grant_requires_canonical_unsigned_decimal_json() -> None:
    vector = _vector()
    grant = dict(vector["grant"])
    grant["maxTotal"] = "01"
    with pytest.raises(BudgetProtocolError):
        SpendGrant.from_json(grant)

    grant["maxTotal"] = "999999999999999999999999999999999999999999999999999999999999999999999999999999"
    with pytest.raises(BudgetProtocolError):
        SpendGrant.from_json(grant)


def test_execution_rejects_nonzero_and_uint256_boundary_violations() -> None:
    vector = _vector()
    execution = dict(vector["execution"])
    execution["amount"] = "0"
    with pytest.raises(BudgetProtocolError):
        PurchaseExecution.from_json(execution)

    execution["amount"] = str(2**256)
    with pytest.raises(BudgetProtocolError):
        PurchaseExecution.from_json(execution)


def test_low_s_and_27_28_signature_rules_are_enforced() -> None:
    vector = _vector()
    grant = SpendGrant.from_json(vector["grant"])
    signature = bytearray.fromhex(vector["owner_signature"][2:])
    signature[64] = 0
    with pytest.raises(BudgetProtocolError):
        recover_grant_signer(grant, bytes(signature), vector["domain"]["chain_id"], vector["domain"]["executor"])

    high_s = bytearray.fromhex(vector["owner_signature"][2:])
    high_s[32:64] = (2**256 - int.from_bytes(high_s[32:64], "big")).to_bytes(32, "big")
    with pytest.raises(BudgetProtocolError):
        recover_grant_signer(grant, bytes(high_s), vector["domain"]["chain_id"], vector["domain"]["executor"])


def test_signing_uses_the_declared_artificial_keys_only_in_memory() -> None:
    vector = _vector()
    domain, grant, execution = _models()
    assert Account.from_key(vector["owner_private_key"]).address.lower() == grant.owner
    assert Account.from_key(vector["execution_private_key"]).address.lower() == grant.execution_signer
    assert sign_grant(grant, vector["owner_private_key"], domain["chain_id"], domain["executor"])
    assert sign_execution(execution, vector["execution_private_key"], domain["chain_id"], domain["executor"])
