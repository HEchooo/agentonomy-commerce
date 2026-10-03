"""Testnet-only first-party policy for the budget-contract rail."""

from __future__ import annotations

from datetime import UTC, timedelta
import hashlib
import json

from services.account_service.schemas import canonicalize_evm_address
from services.policy_service.service import PolicyService


BUDGET_RISK_PROVIDER = "testnet_first_party"
BUDGET_RISK_ENDPOINT = "testnet_first_party_allowlist_v1"
BUDGET_RISK_MAPPING_VERSION = "testnet-first-party-policy-v1"


class BudgetPolicyService(PolicyService):
    """Apply a narrow, explicit allowlist instead of fabricating MistTrack data.

    This policy is deliberately suitable only for the configured TestUSD
    testnet profile.  It preserves ``PolicyService.evaluate`` and all action
    binding/audit behavior while replacing just the provider assessment hook.
    """

    def _assess_risk_provider(self, request, events):
        config = self.config
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        network = request.chain
        resource = metadata.get("resource")
        merchant_id = request.merchant_id or metadata.get("merchant_id")
        token_value = metadata.get("token_address") or metadata.get("asset")
        try:
            token = canonicalize_evm_address(token_value)
        except (TypeError, ValueError):
            token = None
        try:
            payee = canonicalize_evm_address(request.target_address)
        except (TypeError, ValueError):
            payee = None

        reasons: list[str] = []
        if network != config.budget_network:
            reasons.append("network is outside the configured budget testnet")
        if token != config.budget_token_address:
            reasons.append("asset is outside the configured TestUSD contract")
        if payee not in config.budget_allowed_payees:
            reasons.append("payee is outside the first-party test allowlist")
        if merchant_id not in config.budget_allowed_merchant_ids:
            reasons.append("merchant is outside the first-party test allowlist")
        if resource not in config.budget_allowed_resources:
            reasons.append("resource is outside the first-party test allowlist")

        now = self._utc_now()
        expires_at = now + timedelta(seconds=config.risk_max_age_seconds)
        decision = "allow" if not reasons else "deny"
        canonical_scope = {
            "provider": BUDGET_RISK_PROVIDER,
            "endpoint": BUDGET_RISK_ENDPOINT,
            "network": network,
            "token_address": token,
            "payee": payee,
            "merchant_id": merchant_id,
            "resource": resource,
            "decision": decision,
            "reasons": reasons,
        }
        response_sha256 = hashlib.sha256(
            json.dumps(canonical_scope, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assessment = {
            "provider": BUDGET_RISK_PROVIDER,
            "provider_endpoint": BUDGET_RISK_ENDPOINT,
            "subject": payee,
            "network": network,
            "provider_network": network,
            "asset": token,
            "token_address": token,
            "token_symbol": config.budget_token_symbol,
            "coin": config.budget_token_symbol,
            "payee": payee,
            "merchant_id": merchant_id,
            "resource": resource,
            "decision": decision,
            "decision_reasons": list(reasons),
            "mode": "enforce",
            "enforced": True,
            "mapping_version": BUDGET_RISK_MAPPING_VERSION,
            "hold_score": config.risk_hold_score,
            "deny_score": config.risk_deny_score,
            "score": 0 if decision == "allow" else config.risk_deny_score,
            "risk_level": "low" if decision == "allow" else "severe",
            "indicators": [],
            "risk_details": [],
            "hacking_event": None,
            "assessed_at": self._format_time(now),
            "expires_at": self._format_time(expires_at),
            "cache_hit": False,
            "response_sha256": response_sha256,
            "test_only": True,
        }
        events.append(
            {
                "event": "risk_provider_assessed",
                "provider": BUDGET_RISK_PROVIDER,
                "decision": decision,
                "mapping_version": BUDGET_RISK_MAPPING_VERSION,
                "mode": "enforce",
                "enforced": True,
                "created_at": self._format_time(self._utc_now()),
            }
        )
        return assessment
