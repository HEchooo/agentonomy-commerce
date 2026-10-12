"""Public-safe, shared service history for the hosted commerce composition.

This module is deliberately a projection rather than another source of truth.
It accepts only a delivered purchase whose settlement evidence has already been
independently verified by Core and the two RPC watcher paths.  The projection is
stored in the private hosted SQLite database, while ``profile`` exposes only
service identity, aggregate counts, timestamps and public content hashes.
"""
from collections.abc import Mapping
from datetime import UTC, datetime
from fractions import Fraction
import json
import re
import sqlite3

from agentonomy_commerce.budget_network import address


_DIGEST = re.compile(r"^0x[0-9a-fA-F]{64}$")
_SHA256 = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_AGENT_REGISTRY = re.compile(r"^eip155:(\d+):(0x[0-9a-fA-F]{40})$")
_ATOMIC = re.compile(r"^[0-9]+$")
_RECENT_LIMIT = 20


def _field(value, name):
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _hash(value, name):
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a 32-byte hex hash")
    return value.lower()


def _time(value):
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("clock must return a finite timestamp") from None
    if timestamp != timestamp or timestamp in (float("inf"), float("-inf")):
        raise ValueError("clock must return a finite timestamp")
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


def _canonical_agent_id(value):
    if isinstance(value, bool):
        raise ValueError("agent ID must be a non-negative integer")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("agent ID must be a non-negative integer")
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return str(int(value))
    raise ValueError("agent ID must be a non-negative integer")


def _scope(network):
    chain_id = _field(network, "chain_id")
    if isinstance(chain_id, bool) or not isinstance(chain_id, int) or chain_id <= 0:
        raise ValueError("network chain ID is required")
    network_name = _field(network, "network") or f"eip155:{chain_id}"
    if network_name != f"eip155:{chain_id}":
        raise ValueError("network scope is not canonical")
    values = {"chain_id": chain_id, "network": network_name}
    for name in ("token", "executor", "payee"):
        value = _field(network, name)
        try:
            values[name] = address(value)
        except (TypeError, ValueError):
            raise ValueError(f"network {name} is invalid") from None
    return values


def _identity(identity, *, chain_id, payee):
    if not isinstance(identity, Mapping) or identity.get("verified") is not True:
        raise ValueError("verified service identity is required")
    registry = identity.get("agent_registry")
    match = _AGENT_REGISTRY.fullmatch(registry) if isinstance(registry, str) else None
    if match is None or int(match.group(1)) != chain_id:
        raise ValueError("service identity registry does not match the network")
    registry = f"eip155:{chain_id}:{match.group(2).lower()}"
    try:
        wallet = address(identity.get("agent_wallet"))
    except (TypeError, ValueError):
        raise ValueError("service identity wallet is invalid") from None
    if wallet != payee:
        raise ValueError("service identity receiver does not match the configured payee")
    if identity.get("chain_id") != chain_id:
        raise ValueError("service identity chain does not match the network")
    return {
        "agent_registry": registry,
        "agent_id": _canonical_agent_id(identity.get("agent_id")),
        "agent_wallet": wallet,
        "chain_id": chain_id,
    }


def _feedback_db(feedback):
    if feedback is None:
        return None
    if isinstance(feedback, sqlite3.Connection):
        return feedback
    database = getattr(feedback, "db", None)
    return database if isinstance(database, sqlite3.Connection) else None


def _average(scores):
    if not scores:
        return None
    fraction = Fraction(sum(scores), len(scores))
    denominator = fraction.denominator
    count_two = 0
    while denominator % 2 == 0:
        denominator //= 2
        count_two += 1
    count_five = 0
    while denominator % 5 == 0:
        denominator //= 5
        count_five += 1
    scale = max(count_two, count_five)
    factor = 10 ** scale
    if denominator == 1 and factor % fraction.denominator == 0:
        scaled_numerator = fraction.numerator * (factor // fraction.denominator)
        sign = "-" if fraction.numerator < 0 else ""
        integer, remainder = divmod(abs(scaled_numerator), factor)
        if scale == 0 or remainder == 0:
            return sign + str(integer)
        digits = str(remainder).rjust(scale, "0").rstrip("0")
        return f"{sign}{integer}.{digits}"
    return f"{fraction.numerator}/{fraction.denominator}"


class MerchantHistoryStore:
    """Durable cross-tenant projection of independently verified deliveries."""

    def __init__(self, database, *, clock):
        if not isinstance(database, sqlite3.Connection):
            raise TypeError("shared hosted database connection is required")
        if not callable(clock):
            raise TypeError("clock is required")
        self.db, self.clock = database, clock
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS merchant_service_history (
                service_key TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                network TEXT NOT NULL,
                agent_registry TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                payee TEXT NOT NULL,
                token TEXT NOT NULL,
                executor TEXT NOT NULL,
                payment_tx_hash TEXT NOT NULL,
                output_hash TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(service_key, payment_tx_hash)
            )"""
        )
        self.db.execute(
            """CREATE INDEX IF NOT EXISTS merchant_service_history_identity
               ON merchant_service_history(agent_registry, agent_id, chain_id, payee, observed_at)"""
        )
        self.db.commit()

    @staticmethod
    def _service_key(identity, scope):
        return "|".join(
            (identity["agent_registry"], identity["agent_id"], scope["network"], scope["payee"])
        )

    def _verified_delivery(self, *, owner, purchase, identity, scope):
        if not isinstance(purchase, Mapping) or purchase.get("state") != "delivered":
            raise ValueError("delivered purchase required")
        if not isinstance(purchase.get("purchase_id"), str) or not purchase["purchase_id"]:
            raise ValueError("canonical purchase ID is required")
        try:
            owner = address(owner)
        except (TypeError, ValueError):
            raise ValueError("buyer owner is invalid") from None
        if purchase.get("owner") is not None:
            try:
                if address(purchase["owner"]) != owner:
                    raise ValueError("purchase owner mismatch")
            except (TypeError, ValueError):
                raise ValueError("purchase owner mismatch") from None
        output_hash = _hash(purchase.get("output_hash"), "output_hash")
        evidence = purchase.get("settlement")
        if not isinstance(evidence, Mapping):
            raise ValueError("canonical settlement evidence is required")
        if (evidence.get("verified") is not True
            or evidence.get("status") != "verified"
            or evidence.get("two_rpc_verified") is not True
            or type(evidence.get("chain_id")) is not int
            or evidence.get("chain_id") != scope["chain_id"]
            or type(evidence.get("receipt_status")) is not int
            or evidence.get("receipt_status") != 1):
            raise ValueError("independently verified payment evidence is required")
        tx_hash = _hash(evidence.get("transaction_hash"), "transaction_hash")
        for field in ("purchase_commitment", "quote_commitment", "grant_hash",
                      "block_hash", "finality_block_hash"):
            _hash(evidence.get(field), field)
        for key in ("token", "executor", "payee"):
            try:
                actual = address(evidence.get(key))
            except (TypeError, ValueError):
                raise ValueError("canonical payment scope is incomplete") from None
            if actual != scope[key]:
                raise ValueError("payment scope does not match the configured network")
        try:
            evidence_owner = address(evidence.get("owner"))
        except (TypeError, ValueError):
            raise ValueError("canonical payment owner is invalid") from None
        if evidence_owner != owner:
            raise ValueError("payment belongs to another buyer")
        amount = evidence.get("amount_atomic")
        if (not isinstance(amount, str) or _ATOMIC.fullmatch(amount) is None
            or int(amount) <= 0):
            raise ValueError("canonical payment amount is invalid")
        for key, expected in (("network", scope["network"]),
                              ("asset", scope["token"]),
                              ("token_address", scope["token"]),
                              ("pay_to", scope["payee"])):
            if evidence.get(key) is not None:
                actual = evidence[key]
                if key in {"asset", "token_address", "pay_to"}:
                    try:
                        actual = address(actual)
                    except (TypeError, ValueError):
                        raise ValueError("payment scope is malformed") from None
                if actual != expected:
                    raise ValueError("payment scope does not match the configured network")
        return tx_hash, output_hash, owner

    def record_purchase(self, *, tenant_id, owner, purchase, identity, network) -> None:
        """Record one verified delivery without storing private order material.

        ``tenant_id`` is intentionally accepted only as the caller's routing
        context.  It is not persisted because the public history is keyed by
        service and payment identity, and a tenant must not become observable.
        """
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("tenant ID is required")
        scope = _scope(network)
        service_identity = _identity(identity, chain_id=scope["chain_id"], payee=scope["payee"])
        tx_hash, output_hash, _owner = self._verified_delivery(
            owner=owner, purchase=purchase, identity=service_identity, scope=scope
        )
        service_key = self._service_key(service_identity, scope)
        observed_at = _time(self.clock())
        existing = self.db.execute(
            """SELECT output_hash, chain_id, network, agent_registry, agent_id,
                      payee, token, executor
                 FROM merchant_service_history
                WHERE service_key=? AND payment_tx_hash=?""",
            (service_key, tx_hash),
        ).fetchone()
        if existing is not None:
            expected = (output_hash, scope["chain_id"], scope["network"],
                        service_identity["agent_registry"], service_identity["agent_id"],
                        scope["payee"], scope["token"], scope["executor"])
            if tuple(existing) != expected:
                raise ValueError("payment identity is already bound to different service evidence")
            return
        self.db.execute(
            """INSERT INTO merchant_service_history(
                 service_key, chain_id, network, agent_registry, agent_id,
                 payee, token, executor, payment_tx_hash, output_hash, observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (service_key, scope["chain_id"], scope["network"],
             service_identity["agent_registry"], service_identity["agent_id"],
             scope["payee"], scope["token"], scope["executor"], tx_hash,
             output_hash, observed_at),
        )
        self.db.commit()

    def _deliveries(self, service_identity):
        count = self.db.execute(
            """SELECT COUNT(*)
                 FROM merchant_service_history
                WHERE agent_registry=? AND agent_id=? AND chain_id=? AND payee=?""",
            (service_identity["agent_registry"], service_identity["agent_id"],
             service_identity["chain_id"], service_identity["agent_wallet"]),
        ).fetchone()[0]
        rows = self.db.execute(
            """SELECT payment_tx_hash, output_hash, observed_at
                 FROM merchant_service_history
                WHERE agent_registry=? AND agent_id=? AND chain_id=? AND payee=?
                ORDER BY observed_at DESC, payment_tx_hash DESC
                LIMIT ?""",
            (service_identity["agent_registry"], service_identity["agent_id"],
             service_identity["chain_id"], service_identity["agent_wallet"], _RECENT_LIMIT),
        ).fetchall()
        return count, [
            {"payment_tx_hash": row[0], "output_hash": row[1], "observed_at": row[2]}
            for row in rows
        ]

    @staticmethod
    def _feedback_rows(feedback, identity):
        database = _feedback_db(feedback)
        if database is None:
            return [], False
        try:
            rows = database.execute(
                """SELECT buyer, score, document, transaction_hash, status, evidence
                     FROM erc8004_feedback
                    WHERE status='verified'"""
            ).fetchall()
        except sqlite3.OperationalError:
            return [], False
        records = {}
        for row in rows:
            try:
                document = json.loads(row["document"])
                evidence = json.loads(row["evidence"] or "{}")
                buyer = address(row["buyer"])
                score = row["score"]
                if (type(score) is not int or not 0 <= score <= 100
                    or document.get("agentRegistry") != identity["agent_registry"]
                    or _canonical_agent_id(document.get("agentId")) != identity["agent_id"]
                    or evidence.get("verified") is not True
                    or evidence.get("two_rpc_verified") is not True
                    or evidence.get("chain_id") != identity["chain_id"]
                    or evidence.get("agent_registry", identity["agent_registry"])
                    != identity["agent_registry"]
                    or _canonical_agent_id(
                        evidence.get("agent_id", identity["agent_id"])
                    ) != identity["agent_id"]
                    or type(evidence.get("feedback_index")) is not int
                    or evidence["feedback_index"] < 1
                    or type(evidence.get("is_revoked")) is not bool):
                    continue
                feedback_tx = _hash(row["transaction_hash"], "feedback transaction hash")
                observed_feedback_tx = _hash(evidence.get("transaction_hash"), "feedback transaction hash")
                if observed_feedback_tx != feedback_tx:
                    continue
                payment = document.get("proofOfPayment")
                delivery = document.get("delivery")
                if (not isinstance(payment, Mapping) or not isinstance(delivery, Mapping)
                    or address(payment.get("fromAddress")) != buyer
                    or address(payment.get("toAddress")) != identity["agent_wallet"]
                    or payment.get("chainId") != str(identity["chain_id"])
                    or _hash(payment.get("txHash"), "feedback payment hash") is None
                    or not isinstance(delivery.get("outputHash"), str)
                    or _SHA256.fullmatch(delivery["outputHash"]) is None
                    or document.get("value") != score
                    or evidence.get("score", score) != score):
                    continue
                coordinate = (identity["chain_id"], identity["agent_registry"],
                              identity["agent_id"], buyer, evidence["feedback_index"])
                candidate = {
                    "score": score,
                    "two_rpc_verified": True,
                    "verified_at": evidence.get("verified_at"),
                    "is_revoked": evidence["is_revoked"],
                }
                previous = records.get(coordinate)
                # If the same chain coordinate was observed in more than one
                # tenant, a revoked observation wins over an older active
                # copy.  This avoids counting a review that is currently
                # revoked while keeping replayed active rows idempotent.
                if previous is None or (candidate["is_revoked"] and not previous["is_revoked"]):
                    records[coordinate] = candidate
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        valid = [record for record in records.values() if not record["is_revoked"]]
        revoked = sum(1 for record in records.values() if record["is_revoked"])
        return valid, revoked

    def profile(self, *, identity, feedback) -> dict:
        """Return a shared profile with private order context removed."""
        if not isinstance(identity, Mapping) or identity.get("verified") is not True:
            raise ValueError("service identity is required")
        chain_id = identity.get("chain_id")
        if isinstance(chain_id, bool) or not isinstance(chain_id, int) or chain_id <= 0:
            raise ValueError("service identity chain is invalid")
        registry = identity.get("agent_registry")
        match = _AGENT_REGISTRY.fullmatch(registry) if isinstance(registry, str) else None
        if match is None or int(match.group(1)) != chain_id:
            raise ValueError("service identity registry is invalid")
        try:
            wallet = address(identity.get("agent_wallet"))
        except (TypeError, ValueError):
            raise ValueError("service identity wallet is invalid") from None
        service_identity = {
            "agent_registry": f"eip155:{chain_id}:{match.group(2).lower()}",
            "agent_id": _canonical_agent_id(identity.get("agent_id")),
            "agent_wallet": wallet,
            "chain_id": chain_id,
        }
        delivery_count, rows = self._deliveries(service_identity)
        valid_feedback, revoked_feedback = self._feedback_rows(feedback, service_identity)
        scores = [item["score"] for item in valid_feedback]
        feedback_verified_at = [item["verified_at"] for item in valid_feedback
                                if isinstance(item.get("verified_at"), str)]
        as_of = _time(self.clock())
        last_observed = rows[0]["observed_at"] if rows else None
        recent = [
            {"payment_transaction_hash": row["payment_tx_hash"],
             "output_hash": row["output_hash"], "observed_at": row["observed_at"]}
            for row in rows
        ]
        return {
            "status": "verified",
            "service_identity": service_identity,
            "observed_deliveries": delivery_count,
            "sample_size": delivery_count,
            "recent_deliveries": recent,
            "buyer_feedback": {
                "sample_size": len(scores),
                "average_score": _average(scores),
                "revoked_count": revoked_feedback,
                "pending_not_counted": True,
                "verified_two_rpc": bool(valid_feedback or revoked_feedback),
            },
            "rating_guidance": {
                "source": "erc8004_verified_chain_feedback",
                "marketplace_operational_reputation_is_separate": True,
                "marketplace_default_score_50_sample_size_0_is_not_a_buyer_rating": True,
            },
            "evidence": {
                "source": "core_verified_payment_and_sha256_delivery",
                "payment_verification": "two_rpc_verified",
                "two_rpc_verified": bool(rows),
                "delivery_verification": "output_sha256",
                "last_verified_at": last_observed,
            },
            "freshness": {
                "as_of": as_of,
                "last_observed_at": last_observed,
                "feedback_last_verified_at": max(feedback_verified_at) if feedback_verified_at else None,
            },
            "known_limitations": [
                "This is an observed service history, not a guarantee of future delivery.",
                "Marketplace operational reputation is separate; a default score of 50 "
                "with sample_size 0 is not a buyer rating.",
                "Feedback revocation is reflected only when its verified chain evidence is observed again.",
            ],
        }
