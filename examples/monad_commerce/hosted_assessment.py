"""Private persistence for evidence-linked Agent service assessments.

An assessment is an opinion produced by the authenticated consumer Agent after
delivery.  It is deliberately separate from ERC-8004 buyer feedback: this
store does not create a wallet transaction, call an LLM, or claim independent
quality verification.  Only a bounded rubric and public hashes are projected
back to the caller.
"""

from collections.abc import Mapping
import hashlib
import json
import re
import sqlite3

from agentonomy_commerce.budget_network import address

from examples.monad_commerce.hosted_history import (
    MerchantHistoryStore,
    _average,
    _identity,
    _scope,
    _time,
)


ASSESSMENT_RUBRIC_VERSION = "agent-service-v1"
ASSESSMENT_WEIGHTS = {
    "task_fulfillment": 40,
    "result_quality": 40,
    "interaction_quality": 20,
}
ASSESSMENT_SOURCE = "authenticated_consumer_agent"
ASSESSMENT_EVIDENCE_SCOPE = "verified_payment_and_delivery_only"
_ASSESSMENT_KEYS = {
    "rubric_version", "task_fulfillment", "result_quality",
    "interaction_quality", "confidence", "basis_codes",
}
_CONFIDENCE = {"low", "medium", "high"}
_BASIS_CODES = {
    "requirements_checked", "arithmetic_checked", "format_checked",
    "output_hash_checked", "recovery_observed", "single_charge_observed",
    "interaction_reviewed", "limitations_noted",
}
_AGENT_REGISTRY = re.compile(r"^eip155:(\d+):(0x[0-9a-fA-F]{40})$")


def _text(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value


def _canonical_assessment(value):
    if not isinstance(value, Mapping) or set(value) != _ASSESSMENT_KEYS:
        raise ValueError("assessment schema is exact")
    if value["rubric_version"] != ASSESSMENT_RUBRIC_VERSION:
        raise ValueError("unsupported assessment rubric")
    for name in ("task_fulfillment", "result_quality", "interaction_quality"):
        if type(value[name]) is not int or not 0 <= value[name] <= 100:
            raise ValueError(f"{name} must be an integer 0..100")
    if type(value["confidence"]) is not str or value["confidence"] not in _CONFIDENCE:
        raise ValueError("assessment confidence is invalid")
    codes = value["basis_codes"]
    if (not isinstance(codes, list) or not 1 <= len(codes) <= 8
            or any(type(code) is not str or code not in _BASIS_CODES for code in codes)
            or len(set(codes)) != len(codes)):
        raise ValueError("assessment basis_codes are invalid")
    return {
        "rubric_version": ASSESSMENT_RUBRIC_VERSION,
        "task_fulfillment": value["task_fulfillment"],
        "result_quality": value["result_quality"],
        "interaction_quality": value["interaction_quality"],
        "confidence": value["confidence"],
        "basis_codes": list(codes),
    }


def _score(assessment):
    """Apply the fixed 40/40/20 rubric with integer half-up rounding."""
    return (
        40 * assessment["task_fulfillment"]
        + 40 * assessment["result_quality"]
        + 20 * assessment["interaction_quality"]
        + 50
    ) // 100


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _assessment_hash(*, service_key, payment_transaction_hash, output_hash,
                     timestamp, assessment, score):
    payload = {
        "service_key": service_key,
        "payment_transaction_hash": payment_transaction_hash,
        "output_hash": output_hash,
        "timestamp": timestamp,
        "assessment": assessment,
        "score": score,
        "source": ASSESSMENT_SOURCE,
        "on_chain": False,
        "judgment_independently_verified": False,
        "evidence_scope": ASSESSMENT_EVIDENCE_SCOPE,
    }
    return "sha256:" + hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _identity_for_profile(identity):
    if not isinstance(identity, Mapping) or identity.get("verified") is not True:
        raise ValueError("verified service identity is required")
    chain_id = identity.get("chain_id")
    if type(chain_id) is not int or chain_id <= 0:
        raise ValueError("service identity chain is invalid")
    registry = identity.get("agent_registry")
    match = _AGENT_REGISTRY.fullmatch(registry) if isinstance(registry, str) else None
    if match is None or int(match.group(1)) != chain_id:
        raise ValueError("service identity registry is invalid")
    try:
        payee = address(identity.get("agent_wallet"))
    except (TypeError, ValueError):
        raise ValueError("service identity wallet is invalid") from None
    return _identity(identity, chain_id=chain_id, payee=payee)


class AgentAssessmentStore:
    """Durable, private store for evidence-linked Agent opinions.

    The constructor shares the hosted database with ``MerchantHistoryStore``.
    A record is accepted only after that store has observed the same verified
    payment and delivery under the same service scope.
    """

    def __init__(self, database, *, clock):
        if not isinstance(database, sqlite3.Connection):
            raise TypeError("shared hosted database connection is required")
        if not callable(clock):
            raise TypeError("clock is required")
        self.db, self.clock = database, clock
        self.history = MerchantHistoryStore(database, clock=clock)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS agent_service_assessments (
                tenant_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                installation_id TEXT NOT NULL,
                purchase_id TEXT NOT NULL,
                service_key TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                network TEXT NOT NULL,
                agent_registry TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                payee TEXT NOT NULL,
                payment_tx_hash TEXT NOT NULL,
                output_hash TEXT NOT NULL,
                rubric_version TEXT NOT NULL,
                assessment_json TEXT NOT NULL,
                score INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                assessment_hash TEXT NOT NULL,
                source TEXT NOT NULL,
                on_chain INTEGER NOT NULL,
                judgment_independently_verified INTEGER NOT NULL,
                evidence_scope TEXT NOT NULL,
                PRIMARY KEY(tenant_id, purchase_id),
                UNIQUE(service_key, payment_tx_hash)
            )"""
        )
        self.db.execute(
            """CREATE INDEX IF NOT EXISTS agent_service_assessments_identity
               ON agent_service_assessments(agent_registry, agent_id, chain_id, payee, timestamp)"""
        )
        self.db.commit()

    @staticmethod
    def _service_key(identity, scope):
        return MerchantHistoryStore._service_key(identity, scope)

    def _verified_observed_delivery(self, *, owner, purchase, identity, network):
        scope = _scope(network)
        service_identity = _identity(
            identity, chain_id=scope["chain_id"], payee=scope["payee"]
        )
        tx_hash, output_hash, owner = self.history._verified_delivery(
            owner=owner, purchase=purchase, identity=service_identity, scope=scope
        )
        service_key = self._service_key(service_identity, scope)
        row = self.db.execute(
            """SELECT service_key, chain_id, network, agent_registry, agent_id,
                      payee, token, executor, payment_tx_hash, output_hash
                 FROM merchant_service_history
                WHERE service_key=? AND payment_tx_hash=?""",
            (service_key, tx_hash),
        ).fetchone()
        expected = (
            service_key, scope["chain_id"], scope["network"],
            service_identity["agent_registry"], service_identity["agent_id"],
            scope["payee"], scope["token"], scope["executor"], tx_hash, output_hash,
        )
        if row is None or tuple(row) != expected:
            raise ValueError("observed verified delivery is required")
        return scope, service_identity, tx_hash, output_hash, owner, service_key

    @staticmethod
    def _row_assessment(row):
        assessment = json.loads(row["assessment_json"] if isinstance(row, sqlite3.Row)
                                else row[13])
        score = row["score"] if isinstance(row, sqlite3.Row) else row[14]
        timestamp = row["timestamp"] if isinstance(row, sqlite3.Row) else row[15]
        assessment_hash = row["assessment_hash"] if isinstance(row, sqlite3.Row) else row[16]
        payment_tx_hash = row["payment_tx_hash"] if isinstance(row, sqlite3.Row) else row[10]
        output_hash = row["output_hash"] if isinstance(row, sqlite3.Row) else row[11]
        return {
            "status": "assessed",
            "rubric_version": assessment["rubric_version"],
            "task_fulfillment": assessment["task_fulfillment"],
            "result_quality": assessment["result_quality"],
            "interaction_quality": assessment["interaction_quality"],
            "confidence": assessment["confidence"],
            "basis_codes": list(assessment["basis_codes"]),
            "score": score,
            "weights": dict(ASSESSMENT_WEIGHTS),
            "payment_transaction_hash": payment_tx_hash,
            "output_hash": output_hash,
            "timestamp": timestamp,
            "assessment_hash": assessment_hash,
            "source": ASSESSMENT_SOURCE,
            "on_chain": False,
            "judgment_independently_verified": False,
            "evidence_scope": ASSESSMENT_EVIDENCE_SCOPE,
        }

    @staticmethod
    def _assessment_matches(row, assessment, score, service_key, tx_hash, output_hash):
        stored = json.loads(row["assessment_json"] if isinstance(row, sqlite3.Row)
                            else row[13])
        return (
            stored == assessment
            and (row["score"] if isinstance(row, sqlite3.Row) else row[14]) == score
            and (row["service_key"] if isinstance(row, sqlite3.Row) else row[4]) == service_key
            and (row["payment_tx_hash"] if isinstance(row, sqlite3.Row) else row[10]) == tx_hash
            and (row["output_hash"] if isinstance(row, sqlite3.Row) else row[11]) == output_hash
        )

    def record(self, *, tenant_id, owner, installation_id, purchase, identity,
               network, assessment):
        tenant_id = _text(tenant_id, "tenant ID")
        installation_id = _text(installation_id, "installation ID")
        if not isinstance(purchase, Mapping):
            raise ValueError("purchase is required")
        purchase_id = _text(purchase.get("purchase_id"), "purchase ID")
        try:
            owner = address(owner)
        except (TypeError, ValueError):
            raise ValueError("buyer owner is invalid") from None
        normalized = _canonical_assessment(assessment)
        scope, service_identity, tx_hash, output_hash, owner, service_key = (
            self._verified_observed_delivery(
                owner=owner, purchase=purchase, identity=identity, network=network
            )
        )
        score = _score(normalized)
        existing = self.db.execute(
            """SELECT * FROM agent_service_assessments
                WHERE tenant_id=? AND purchase_id=?""",
            (tenant_id, purchase_id),
        ).fetchone()
        if existing is not None:
            existing_owner = existing["owner"] if isinstance(existing, sqlite3.Row) else existing[1]
            existing_installation = existing["installation_id"] if isinstance(existing, sqlite3.Row) else existing[2]
            if existing_owner != owner or existing_installation != installation_id:
                raise PermissionError("assessment belongs to another device or wallet")
            if not self._assessment_matches(existing, normalized, score, service_key, tx_hash, output_hash):
                raise ValueError("assessment is frozen")
            return self._row_assessment(existing)

        payment_existing = self.db.execute(
            """SELECT * FROM agent_service_assessments
                WHERE service_key=? AND payment_tx_hash=?""",
            (service_key, tx_hash),
        ).fetchone()
        if payment_existing is not None:
            raise PermissionError("payment assessment belongs to another tenant or device")

        timestamp = _time(self.clock())
        digest = _assessment_hash(
            service_key=service_key, payment_transaction_hash=tx_hash,
            output_hash=output_hash, timestamp=timestamp,
            assessment=normalized, score=score,
        )
        self.db.execute(
            """INSERT INTO agent_service_assessments(
                 tenant_id, owner, installation_id, purchase_id, service_key,
                 chain_id, network, agent_registry, agent_id, payee,
                 payment_tx_hash, output_hash, rubric_version, assessment_json,
                 score, timestamp, assessment_hash, source, on_chain,
                 judgment_independently_verified, evidence_scope)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tenant_id, owner, installation_id, purchase_id, service_key,
                scope["chain_id"], scope["network"], service_identity["agent_registry"],
                service_identity["agent_id"], scope["payee"], tx_hash, output_hash,
                ASSESSMENT_RUBRIC_VERSION, _canonical_json(normalized), score,
                timestamp, digest, ASSESSMENT_SOURCE, 0, 0, ASSESSMENT_EVIDENCE_SCOPE,
            ),
        )
        self.db.commit()
        row = self.db.execute(
            """SELECT * FROM agent_service_assessments
                WHERE tenant_id=? AND purchase_id=?""",
            (tenant_id, purchase_id),
        ).fetchone()
        return self._row_assessment(row)

    def get(self, *, tenant_id, owner, installation_id, purchase_id):
        tenant_id = _text(tenant_id, "tenant ID")
        installation_id = _text(installation_id, "installation ID")
        purchase_id = _text(purchase_id, "purchase ID")
        try:
            owner = address(owner)
        except (TypeError, ValueError):
            raise ValueError("buyer owner is invalid") from None
        row = self.db.execute(
            """SELECT * FROM agent_service_assessments
                WHERE tenant_id=? AND purchase_id=?""",
            (tenant_id, purchase_id),
        ).fetchone()
        if row is None:
            return {"status": "unassessed"}
        stored_owner = row["owner"] if isinstance(row, sqlite3.Row) else row[1]
        stored_installation = row["installation_id"] if isinstance(row, sqlite3.Row) else row[2]
        if stored_owner != owner or stored_installation != installation_id:
            raise PermissionError("assessment belongs to another device or wallet")
        return self._row_assessment(row)

    def profile(self, *, identity):
        service_identity = _identity_for_profile(identity)
        scope = {"network": f"eip155:{service_identity['chain_id']}",
                 "payee": service_identity["agent_wallet"]}
        service_key = self._service_key(service_identity, scope)
        count = self.db.execute(
            """SELECT COUNT(*) FROM agent_service_assessments
                WHERE service_key=? AND rubric_version=?""",
            (service_key, ASSESSMENT_RUBRIC_VERSION),
        ).fetchone()[0]
        rows = self.db.execute(
            """SELECT * FROM agent_service_assessments
                WHERE service_key=? AND rubric_version=?
                ORDER BY timestamp DESC, assessment_hash DESC
                LIMIT 20""",
            (service_key, ASSESSMENT_RUBRIC_VERSION),
        ).fetchall()
        scores = [row["score"] if isinstance(row, sqlite3.Row) else row[14] for row in rows]
        # Re-read all scores when the history exceeds the public recent window.
        if count > len(rows):
            scores = [
                row[0] for row in self.db.execute(
                    """SELECT score FROM agent_service_assessments
                        WHERE service_key=? AND rubric_version=?""",
                    (service_key, ASSESSMENT_RUBRIC_VERSION),
                ).fetchall()
            ]
        return {
            "status": "assessed" if count else "unassessed",
            "service_identity": service_identity,
            "rubric_version": ASSESSMENT_RUBRIC_VERSION,
            "weights": dict(ASSESSMENT_WEIGHTS),
            "sample_size": count,
            "average_score": _average(scores),
            "recent_assessments": [self._row_assessment(row) for row in rows],
            "source": ASSESSMENT_SOURCE,
            "on_chain": False,
            "judgment_independently_verified": False,
            "evidence_scope": ASSESSMENT_EVIDENCE_SCOPE,
        }

