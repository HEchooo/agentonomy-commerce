"""Focused tests for the offline Agent service assessment projection."""

import json
import re
import sqlite3
from types import SimpleNamespace

import pytest

from examples.monad_commerce.hosted_assessment import AgentAssessmentStore
from examples.monad_commerce.hosted_history import MerchantHistoryStore


CHAIN_ID = 10143
TOKEN = "0x" + "11" * 20
EXECUTOR = "0x" + "22" * 20
PAYEE = "0x" + "33" * 20
OWNER_A = "0x" + "aa" * 20
OWNER_B = "0x" + "bb" * 20
AGENT_REGISTRY = f"eip155:{CHAIN_ID}:0x" + "99" * 20
OTHER_REGISTRY = f"eip155:{CHAIN_ID}:0x" + "98" * 20
INSTALLATION_A = "opc_a"
INSTALLATION_B = "opc_b"
PAYMENT_A = "0x" + "12" * 32
PAYMENT_B = "0x" + "13" * 32
OUTPUT_A = "0x" + "ab" * 32
OUTPUT_B = "0x" + "cd" * 32


class Clock:
    def __init__(self, value=1_760_000_000):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture
def network():
    return SimpleNamespace(
        network=f"eip155:{CHAIN_ID}", chain_id=CHAIN_ID, token=TOKEN,
        executor=EXECUTOR, payee=PAYEE,
    )


@pytest.fixture
def identity():
    return {
        "verified": True,
        "agent_registry": AGENT_REGISTRY,
        "agent_id": 2073,
        "agent_wallet": PAYEE,
        "chain_id": CHAIN_ID,
    }


def delivered(owner, tx_hash=PAYMENT_A, output_hash=OUTPUT_A, *, state="delivered",
              purchase_id="purchase_private_should_not_escape"):
    return {
        "purchase_id": purchase_id,
        "preview_id": "preview_private_should_not_escape",
        "state": state,
        "input_hash": "0x" + "44" * 32,
        "output_hash": output_hash,
        "service_result": {"private_csv": "never publish"},
        "settlement": {
            "verified": True,
            "status": "verified",
            "two_rpc_verified": True,
            "chain_id": CHAIN_ID,
            "receipt_status": 1,
            "transaction_hash": tx_hash,
            "token": TOKEN,
            "executor": EXECUTOR,
            "payee": PAYEE,
            "owner": owner,
            "amount_atomic": "300000",
            "purchase_commitment": "0x" + "56" * 32,
            "quote_commitment": "0x" + "57" * 32,
            "grant_hash": "0x" + "58" * 32,
            "block_hash": "0x" + "59" * 32,
            "finality_block_hash": "0x" + "5a" * 32,
        },
    }


def assessment(**changes):
    value = {
        "rubric_version": "agent-service-v1",
        "task_fulfillment": 90,
        "result_quality": 80,
        "interaction_quality": 70,
        "confidence": "high",
        "basis_codes": [
            "requirements_checked", "arithmetic_checked", "format_checked",
            "output_hash_checked", "single_charge_observed",
        ],
    }
    value.update(changes)
    return value


def prepared_store(network, identity, clock):
    db = sqlite3.connect(":memory:")
    history = MerchantHistoryStore(db, clock=clock)
    purchase = delivered(OWNER_A)
    history.record_purchase(
        tenant_id="tenant_a", owner=OWNER_A, purchase=purchase,
        identity=identity, network=network,
    )
    return db, AgentAssessmentStore(db, clock=clock), purchase


def test_record_requires_observed_verified_delivery_and_returns_public_view(network, identity):
    clock = Clock()
    db = sqlite3.connect(":memory:")
    store = AgentAssessmentStore(db, clock=clock)
    purchase = delivered(OWNER_A)
    with pytest.raises(ValueError, match="observed verified delivery"):
        store.record(
            tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
            purchase=purchase, identity=identity, network=network,
            assessment=assessment(),
        )

    MerchantHistoryStore(db, clock=clock).record_purchase(
        tenant_id="tenant_a", owner=OWNER_A, purchase=purchase,
        identity=identity, network=network,
    )
    view = store.record(
        tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
        purchase=purchase, identity=identity, network=network,
        assessment=assessment(),
    )
    assert view["status"] == "assessed"
    assert view["score"] == 82
    assert view["payment_transaction_hash"] == PAYMENT_A
    assert view["output_hash"] == OUTPUT_A
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", view["assessment_hash"])
    assert view["source"] == "authenticated_consumer_agent"
    assert view["on_chain"] is False
    assert view["judgment_independently_verified"] is False
    assert view["evidence_scope"] == "verified_payment_and_delivery_only"

    serialized = json.dumps(view)
    for private in (
        "purchase_private_should_not_escape", "tenant_a", INSTALLATION_A,
        OWNER_A, "never publish", "0x" + "44" * 32,
    ):
        assert private not in serialized


@pytest.mark.parametrize(
    "change",
    [
        {"extra": True},
        {"rubric_version": "other"},
        {"task_fulfillment": True},
        {"result_quality": 101},
        {"interaction_quality": -1},
        {"confidence": "certain"},
        {"confidence": []},
        {"confidence": {}},
        {"confidence": None},
        {"basis_codes": []},
        {"basis_codes": ["requirements_checked", "requirements_checked"]},
        {"basis_codes": ["unknown_code"]},
    ],
)
def test_assessment_schema_is_exact_and_fails_closed(network, identity, change):
    clock = Clock()
    db, store, purchase = prepared_store(network, identity, clock)
    candidate = assessment()
    if "extra" in change:
        candidate["extra"] = change["extra"]
    else:
        candidate.update(change)
    with pytest.raises(ValueError):
        store.record(
            tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
            purchase=purchase, identity=identity, network=network,
            assessment=candidate,
        )
    db.close()


def test_pending_or_unverified_delivery_cannot_be_assessed(network, identity):
    clock = Clock()
    db = sqlite3.connect(":memory:")
    store = AgentAssessmentStore(db, clock=clock)
    history = MerchantHistoryStore(db, clock=clock)
    pending = delivered(OWNER_A, state="paid")
    for candidate in (
        pending,
        {**delivered(OWNER_A), "settlement": {**delivered(OWNER_A)["settlement"], "verified": False}},
    ):
        with pytest.raises(ValueError):
            store.record(
                tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
                purchase=candidate, identity=identity, network=network,
                assessment=assessment(),
            )


def test_replay_is_exact_and_conflicts_or_other_devices_are_rejected(network, identity):
    clock = Clock()
    db, store, purchase = prepared_store(network, identity, clock)
    first = store.record(
        tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
        purchase=purchase, identity=identity, network=network,
        assessment=assessment(),
    )
    clock.value += 100
    replay = store.record(
        tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
        purchase=purchase, identity=identity, network=network,
        assessment=assessment(),
    )
    assert replay == first
    assert store.get(
        tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
        purchase_id=purchase["purchase_id"],
    ) == first
    with pytest.raises(ValueError, match="frozen"):
        store.record(
            tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
            purchase=purchase, identity=identity, network=network,
            assessment=assessment(task_fulfillment=91),
        )
    with pytest.raises(PermissionError):
        store.get(
            tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_B,
            purchase_id=purchase["purchase_id"],
        )
    assert store.get(
        tenant_id="other_tenant", owner=OWNER_A, installation_id=INSTALLATION_A,
        purchase_id=purchase["purchase_id"],
    ) == {"status": "unassessed"}


def test_restart_preserves_record_and_cross_tenant_payment_isolation(network, identity, tmp_path):
    clock = Clock()
    path = tmp_path / "assessment.sqlite3"
    db = sqlite3.connect(path)
    history = MerchantHistoryStore(db, clock=clock)
    purchase = delivered(OWNER_A)
    history.record_purchase(
        tenant_id="tenant_a", owner=OWNER_A, purchase=purchase,
        identity=identity, network=network,
    )
    store = AgentAssessmentStore(db, clock=clock)
    first = store.record(
        tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
        purchase=purchase, identity=identity, network=network,
        assessment=assessment(),
    )
    db.close()

    db2 = sqlite3.connect(path)
    restored = AgentAssessmentStore(db2, clock=clock)
    assert restored.get(
        tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
        purchase_id=purchase["purchase_id"],
    ) == first

    # A second tenant cannot bind a new assessment to the same service/payment.
    with pytest.raises(PermissionError):
        restored.record(
            tenant_id="tenant_b", owner=OWNER_A, installation_id=INSTALLATION_A,
            purchase=purchase, identity=identity, network=network,
            assessment=assessment(),
        )
    assert first["assessment_hash"].startswith("sha256:")


def test_profile_uses_exact_average_and_isolates_service_identity(network, identity):
    clock = Clock()
    db, store, purchase_a = prepared_store(network, identity, clock)
    store.record(
        tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
        purchase=purchase_a, identity=identity, network=network,
        assessment=assessment(task_fulfillment=90, result_quality=80, interaction_quality=70),
    )
    purchase_b = delivered(OWNER_B, PAYMENT_B, OUTPUT_B)
    MerchantHistoryStore(db, clock=clock).record_purchase(
        tenant_id="tenant_b", owner=OWNER_B, purchase=purchase_b,
        identity=identity, network=network,
    )
    clock.value += 1
    store.record(
        tenant_id="tenant_b", owner=OWNER_B, installation_id=INSTALLATION_B,
        purchase=purchase_b, identity=identity, network=network,
        assessment=assessment(task_fulfillment=91, result_quality=81, interaction_quality=71),
    )
    profile = store.profile(identity=identity)
    assert profile["sample_size"] == 2
    assert profile["average_score"] == "82.5"
    assert len(profile["recent_assessments"]) == 2
    assert profile["rubric_version"] == "agent-service-v1"
    assert profile["weights"] == {
        "task_fulfillment": 40, "result_quality": 40, "interaction_quality": 20,
    }
    assert OWNER_A not in json.dumps(profile)
    assert "tenant_a" not in json.dumps(profile)
    isolated = dict(identity, agent_registry=OTHER_REGISTRY)
    assert store.profile(identity=isolated)["sample_size"] == 0


def test_profile_aggregates_all_23_records_but_limits_recent_projection(network, identity):
    clock = Clock()
    db, store, first_purchase = prepared_store(network, identity, clock)
    history = MerchantHistoryStore(db, clock=clock)
    scores = [70, 90, 80] + [82] * 14 + [83] * 6
    for index, score in enumerate(scores):
        if index == 0:
            purchase = first_purchase
        else:
            clock.value += 1
            purchase = delivered(
                OWNER_A,
                tx_hash="0x" + f"{index + 1000:064x}",
                output_hash="0x" + f"{index + 2000:064x}",
                purchase_id=f"purchase_{index}",
            )
            history.record_purchase(
                tenant_id=f"tenant_{index}", owner=OWNER_A, purchase=purchase,
                identity=identity, network=network,
            )
        store.record(
            tenant_id=f"tenant_{index}", owner=OWNER_A,
            installation_id=f"installation_{index}", purchase=purchase,
            identity=identity, network=network,
            assessment=assessment(
                task_fulfillment=score, result_quality=score,
                interaction_quality=score,
            ),
        )

    profile = store.profile(identity=identity)
    assert profile["sample_size"] == 23
    assert profile["average_score"] == "82"
    assert len(profile["recent_assessments"]) == 20
    assert profile["recent_assessments"][0]["score"] == 83
    assert store.profile(identity=identity) == profile


def test_profile_excludes_a_persisted_future_rubric_from_current_aggregate(network, identity):
    clock = Clock()
    db, store, purchase = prepared_store(network, identity, clock)
    current = store.record(
        tenant_id="tenant_a", owner=OWNER_A, installation_id=INSTALLATION_A,
        purchase=purchase, identity=identity, network=network,
        assessment=assessment(),
    )
    service_key = db.execute(
        "SELECT service_key FROM agent_service_assessments WHERE purchase_id=?",
        (purchase["purchase_id"],),
    ).fetchone()[0]
    legacy_payment = "0x" + "ee" * 32
    legacy_output = "0x" + "ef" * 32
    legacy_assessment = {
        "rubric_version": "agent-service-v2",
        "task_fulfillment": 100,
        "result_quality": 100,
        "interaction_quality": 100,
        "confidence": "high",
        "basis_codes": ["requirements_checked"],
    }
    db.execute(
        """INSERT INTO agent_service_assessments(
             tenant_id, owner, installation_id, purchase_id, service_key,
             chain_id, network, agent_registry, agent_id, payee,
             payment_tx_hash, output_hash, rubric_version, assessment_json,
             score, timestamp, assessment_hash, source, on_chain,
             judgment_independently_verified, evidence_scope)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "legacy_tenant", OWNER_B, "legacy_installation", "legacy_purchase",
            service_key, CHAIN_ID, f"eip155:{CHAIN_ID}", AGENT_REGISTRY, "2073",
            PAYEE, legacy_payment, legacy_output, "agent-service-v2",
            json.dumps(legacy_assessment, sort_keys=True, separators=(",", ":")),
            100, "2999-01-01T00:00:00Z", "sha256:" + "9" * 64,
            "authenticated_consumer_agent", 0, 0, "verified_payment_and_delivery_only",
        ),
    )
    db.commit()

    profile = store.profile(identity=identity)
    assert profile["sample_size"] == 1
    assert profile["average_score"] == str(current["score"])
    assert len(profile["recent_assessments"]) == 1
    assert profile["recent_assessments"][0]["rubric_version"] == "agent-service-v1"

