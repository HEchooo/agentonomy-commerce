"""Tests for the shared public-safe merchant history projection."""
import json
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from examples.monad_commerce.hosted_feedback import FeedbackStore
from examples.monad_commerce.hosted_history import MerchantHistoryStore, _average


CHAIN_ID = 10143
TOKEN = "0x" + "11" * 20
EXECUTOR = "0x" + "22" * 20
PAYEE = "0x" + "33" * 20
OWNER_A = "0x" + "aa" * 20
OWNER_B = "0x" + "bb" * 20
AGENT_REGISTRY = f"eip155:{CHAIN_ID}:0x" + "99" * 20
AGENT_ID = 2073
PAYMENT_A = "0x" + "12" * 32
PAYMENT_B = "0x" + "13" * 32
OUTPUT_A = "0x" + "ab" * 32
OUTPUT_B = "0x" + "cd" * 32
FEEDBACK_TX_A = "0x" + "34" * 32


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
        "agent_id": AGENT_ID,
        "agent_wallet": PAYEE,
        "chain_id": CHAIN_ID,
    }


def delivered(owner, tx_hash, output_hash):
    return {
        "purchase_id": "purchase_private_should_not_escape",
        "preview_id": "preview_private_should_not_escape",
        "state": "delivered",
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


def feedback_document(*, buyer=OWNER_A, score=87, payment=PAYMENT_A,
                      output=OUTPUT_A, agent_registry=AGENT_REGISTRY,
                      agent_id=AGENT_ID, payee=PAYEE, chain_id=CHAIN_ID):
    return {
        "agentRegistry": agent_registry,
        "agentId": agent_id,
        "clientAddress": f"eip155:{chain_id}:{buyer}",
        "value": score,
        "valueDecimals": 0,
        "proofOfPayment": {
            "fromAddress": buyer,
            "toAddress": payee,
            "chainId": str(chain_id),
            "txHash": payment,
        },
        "delivery": {"outputHash": "sha256:" + output[2:]},
    }


def insert_feedback(db, *, status="verified", evidence=None, document=None,
                    tenant="tenant_a", purchase="purchase_a", buyer=OWNER_A,
                    feedback_tx=FEEDBACK_TX_A):
    document = document or feedback_document(buyer=buyer)
    digest = "0x" + hashlib.sha256(f"{tenant}:{purchase}:{feedback_tx}".encode()).hexdigest()
    db.execute(
        "INSERT INTO erc8004_feedback "
        "(tenant_id,purchase_id,buyer,score,document,feedback_hash,"
        "transaction_payload,transaction_hash,status,evidence) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tenant, purchase, buyer, document["value"], json.dumps(document),
         digest, "{}", feedback_tx, status,
         None if evidence is None else json.dumps(evidence)),
    )
    db.commit()


def verified_evidence(*, tx_hash=FEEDBACK_TX_A, score=87, revoked=False,
                      chain_id=CHAIN_ID, agent_registry=AGENT_REGISTRY,
                      agent_id=AGENT_ID):
    return {
        "verified": True,
        "two_rpc_verified": True,
        "transaction_hash": tx_hash,
        "feedback_index": 1,
        "is_revoked": revoked,
        "score": score,
        "chain_id": chain_id,
        "agent_registry": agent_registry,
        "agent_id": agent_id,
    }


@pytest.mark.parametrize(
    ("scores", "expected"),
    [([87, 88], "87.5"), ([87, 88, 87, 88], "87.5"),
     ([87, 88, 87, 87], "87.25"), ([0, 0, 1, 0, 0, 0, 0, 0], "0.125"),
     ([0, 1, 0], "1/3")],
)
def test_feedback_average_is_exact_without_float_rounding(scores, expected):
    assert _average(scores) == expected


def test_verified_delivery_requires_strict_core_proof_and_nonzero_amount(network, identity):
    db = sqlite3.connect(":memory:")
    store = MerchantHistoryStore(db, clock=Clock())
    purchase = delivered(OWNER_A, PAYMENT_A, OUTPUT_A)
    required_proof = (
        "purchase_commitment", "quote_commitment", "grant_hash",
        "block_hash", "finality_block_hash",
    )
    for field in required_proof:
        evidence = dict(purchase["settlement"])
        evidence.pop(field)
        with pytest.raises(ValueError):
            store.record_purchase(
                tenant_id="tenant_a", owner=OWNER_A,
                purchase=dict(purchase, settlement=evidence),
                identity=identity, network=network,
            )
    for field, invalid in (("verified", 1), ("two_rpc_verified", 1),
                           ("receipt_status", True), ("amount_atomic", "0")):
        evidence = dict(purchase["settlement"], **{field: invalid})
        with pytest.raises(ValueError):
            store.record_purchase(
                tenant_id="tenant_a", owner=OWNER_A,
                purchase=dict(purchase, settlement=evidence),
                identity=identity, network=network,
            )


def test_payment_identity_dedupes_replays_but_rejects_conflicting_output(network, identity):
    db = sqlite3.connect(":memory:")
    store = MerchantHistoryStore(db, clock=Clock())
    purchase = delivered(OWNER_A, PAYMENT_A, OUTPUT_A)
    store.record_purchase(tenant_id="tenant_a", owner=OWNER_A,
                          purchase=purchase, identity=identity, network=network)
    replay = dict(purchase, purchase_id="another_private_purchase",
                  preview_id="another_private_preview",
                  input_hash="0x" + "45" * 32)
    store.record_purchase(tenant_id="tenant_b", owner=OWNER_A,
                          purchase=replay, identity=identity, network=network)
    with pytest.raises(ValueError):
        store.record_purchase(
            tenant_id="tenant_b", owner=OWNER_A,
            purchase=dict(purchase, output_hash=OUTPUT_B),
            identity=identity, network=network,
        )
    assert store.profile(identity=identity, feedback=None)["observed_deliveries"] == 1


def test_recent_history_is_bounded_but_observed_count_is_complete(network, identity):
    db = sqlite3.connect(":memory:")
    clock = Clock()
    store = MerchantHistoryStore(db, clock=clock)
    for index in range(25):
        tx_hash = "0x" + f"{index + 100:064x}"
        output_hash = "0x" + f"{index + 200:064x}"
        store.record_purchase(
            tenant_id=f"tenant_{index}", owner=OWNER_A,
            purchase=delivered(OWNER_A, tx_hash, output_hash),
            identity=identity, network=network,
        )
        clock.value += 1
    profile = store.profile(identity=identity, feedback=None)
    assert profile["observed_deliveries"] == 25
    assert len(profile["recent_deliveries"]) == 20
    assert profile["recent_deliveries"][0]["payment_transaction_hash"] == "0x" + f"{124:064x}"


def test_recording_is_shared_private_and_replay_safe(tmp_path, network, identity):
    path = tmp_path / "sessions.sqlite3"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    clock = Clock()
    store = MerchantHistoryStore(db, clock=clock)
    first = delivered(OWNER_A, PAYMENT_A, OUTPUT_A)
    second = delivered(OWNER_B, PAYMENT_B, OUTPUT_B)

    store.record_purchase(tenant_id="tenant_a", owner=OWNER_A, purchase=first,
                          identity=identity, network=network)
    clock.value += 30
    store.record_purchase(tenant_id="tenant_b", owner=OWNER_B, purchase=second,
                          identity=identity, network=network)
    store.record_purchase(tenant_id="tenant_a", owner=OWNER_A, purchase=first,
                          identity=identity, network=network)
    db.close()

    reopened = sqlite3.connect(path)
    reopened.row_factory = sqlite3.Row
    restored = MerchantHistoryStore(reopened, clock=clock)
    profile = restored.profile(identity=identity, feedback=None)
    assert profile["observed_deliveries"] == 2
    assert profile["sample_size"] == 2
    assert len(profile["recent_deliveries"]) == 2
    serialized = json.dumps(profile)
    for secret in ("purchase_private_should_not_escape", "preview_private_should_not_escape",
                   "never publish", "0x" + "44" * 32, "tenant_a", "tenant_b", OWNER_A, OWNER_B):
        assert secret not in serialized
    assert PAYMENT_A in serialized and PAYMENT_B in serialized
    assert OUTPUT_A in serialized and OUTPUT_B in serialized


def test_recording_fails_closed_for_unverified_or_mismatched_facts(network, identity):
    db = sqlite3.connect(":memory:")
    store = MerchantHistoryStore(db, clock=Clock())
    purchase = delivered(OWNER_A, PAYMENT_A, OUTPUT_A)
    for patch in (
        {"state": "paid_but_undelivered"},
        {"settlement": {**purchase["settlement"], "verified": False}},
        {"settlement": {**purchase["settlement"], "two_rpc_verified": False}},
        {"settlement": {**purchase["settlement"], "owner": OWNER_B}},
        {"settlement": {**purchase["settlement"], "token": "0x" + "66" * 20}},
        {"settlement": {**purchase["settlement"], "payee": "0x" + "77" * 20}},
        {"output_hash": "sha256:" + "ab" * 32},
    ):
        candidate = dict(purchase, **patch)
        with pytest.raises(ValueError):
            store.record_purchase(tenant_id="tenant_a", owner=OWNER_A,
                                  purchase=candidate, identity=identity, network=network)
    with pytest.raises(ValueError):
        store.record_purchase(tenant_id="tenant_a", owner=OWNER_B,
                              purchase=purchase, identity=identity, network=network)
    with pytest.raises(ValueError):
        store.record_purchase(tenant_id="tenant_a", owner=OWNER_A,
                              purchase=purchase,
                              identity={**identity, "verified": False}, network=network)


def test_verified_feedback_is_aggregated_once_pending_and_revoked_are_excluded(
    network, identity
):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    feedback = FeedbackStore.__new__(FeedbackStore)
    feedback.db = db
    db.execute("""CREATE TABLE erc8004_feedback (
        tenant_id TEXT NOT NULL, purchase_id TEXT NOT NULL, buyer TEXT NOT NULL,
        score INTEGER NOT NULL, document TEXT NOT NULL, feedback_hash TEXT UNIQUE NOT NULL,
        transaction_payload TEXT NOT NULL, transaction_hash TEXT, status TEXT NOT NULL,
        evidence TEXT, PRIMARY KEY(tenant_id, purchase_id))""")
    insert_feedback(db, evidence=verified_evidence())
    # The same chain coordinate under another tenant must count once.
    insert_feedback(db, tenant="tenant_b", purchase="purchase_b",
                    feedback_tx=FEEDBACK_TX_A, evidence=verified_evidence())
    insert_feedback(db, tenant="tenant_c", purchase="purchase_c", status="pending",
                    feedback_tx="0x" + "35" * 32, evidence=verified_evidence(tx_hash="0x" + "35" * 32))
    insert_feedback(db, tenant="tenant_d", purchase="purchase_d",
                    buyer=OWNER_B, document=feedback_document(buyer=OWNER_B, score=99),
                    feedback_tx="0x" + "36" * 32,
                    evidence=verified_evidence(tx_hash="0x" + "36" * 32, score=99, revoked=True))
    store = MerchantHistoryStore(db, clock=Clock())
    store.record_purchase(tenant_id="tenant_a", owner=OWNER_A,
                          purchase=delivered(OWNER_A, PAYMENT_A, OUTPUT_A),
                          identity=identity, network=network)
    profile = store.profile(identity=identity, feedback=feedback)
    assert profile["buyer_feedback"]["sample_size"] == 1
    assert profile["buyer_feedback"]["average_score"] == "87"
    assert profile["buyer_feedback"]["revoked_count"] == 1
    assert profile["buyer_feedback"]["pending_not_counted"] is True
    assert profile["buyer_feedback"]["verified_two_rpc"] is True


def test_feedback_identity_payee_and_chain_mismatches_are_not_published(network, identity):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE erc8004_feedback (
        tenant_id TEXT NOT NULL, purchase_id TEXT NOT NULL, buyer TEXT NOT NULL,
        score INTEGER NOT NULL, document TEXT NOT NULL, feedback_hash TEXT UNIQUE NOT NULL,
        transaction_payload TEXT NOT NULL, transaction_hash TEXT, status TEXT NOT NULL,
        evidence TEXT, PRIMARY KEY(tenant_id, purchase_id))""")
    feedback = SimpleNamespace(db=db)
    insert_feedback(db, document=feedback_document(payee=OWNER_B),
                    evidence=verified_evidence())
    insert_feedback(db, tenant="tenant_b", purchase="purchase_b",
                    document=feedback_document(chain_id=1),
                    feedback_tx="0x" + "35" * 32,
                    evidence=verified_evidence(tx_hash="0x" + "35" * 32, chain_id=1))
    store = MerchantHistoryStore(db, clock=Clock())
    profile = store.profile(identity=identity, feedback=feedback)
    assert profile["buyer_feedback"]["sample_size"] == 0
    assert profile["buyer_feedback"]["average_score"] is None


def test_no_feedback_table_is_a_valid_empty_profile(network, identity):
    db = sqlite3.connect(":memory:")
    store = MerchantHistoryStore(db, clock=Clock())
    profile = store.profile(identity=identity, feedback=None)
    assert profile["buyer_feedback"]["sample_size"] == 0
    assert profile["buyer_feedback"]["average_score"] is None
    assert profile["freshness"]["as_of"].endswith("Z")
