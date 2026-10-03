"""Core Funding rail for the user-signed TestUSD budget executor."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Protocol

from sqlalchemy import select

from services.account_service.repository import SpendingGrantRow, WalletIdentityRow
from services.account_service.schemas import canonicalize_evm_address
from services.funding_service.budget_binding import (
    BudgetBinding,
    _canonical_grant_payload,
    _grant_model,
    _grant_value,
    binding_id_for,
    canonical_bytes32,
    protocol_grant_hash,
    validate_signed_budget_attempt,
    verify_owner_signature,
)
from services.funding_service.ledger import (
    canonicalize_transaction_hash,
    require_unified_reservation,
)
from services.funding_service.schemas import (
    ReleaseSpendingReservationRequest,
    SettleSpendingReservationRequest,
)
from services.funding_service.service import FundingService
from services.policy_service.budget_policy import (
    BUDGET_RISK_ENDPOINT,
    BUDGET_RISK_MAPPING_VERSION,
    BUDGET_RISK_PROVIDER,
    BudgetPolicyService,
)
from shared.budget_config import BudgetAppConfig
from shared.canonical_assets import CanonicalAssetRegistry


class BudgetBackend(Protocol):
    relayer_address: str
    execution_signer_address: str

    def prepare(self, row: dict, binding: BudgetBinding, nonce: int) -> dict: ...

    def broadcast(self, attempt: dict) -> str: ...

    def verify(
        self, row: dict, binding: BudgetBinding, attempt: dict
    ) -> Mapping[str, Any] | None: ...

    def pending_nonce(self) -> int: ...

    def allowance(self, owner: str) -> int: ...


_POSITIVE_UINT = re.compile(r"^[1-9][0-9]*$")
_BUDGET_RAIL = "budget_contract"
_BUDGET_BINDING_RECORD = "budget_binding"
_PUBLIC_BUDGET_ATTEMPT_FIELDS = (
    "tx_hash",
    "relayer",
    "nonce",
    "execution_digest",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if hasattr(value, "to_json"):
        return _json_value(value.to_json())
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ValueError("budget attempt contains a non-serializable value")


def _authorization_hash(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"))
    return "0x" + hashlib.sha256(canonical.encode()).hexdigest()


class BudgetFundingService(FundingService):
    """Reuse Core reservation, ledger and receipt code with a sealed budget rail."""

    def __init__(
        self,
        config: BudgetAppConfig,
        storage_file=None,
        rpc_transport=None,
        policy_service=None,
        backend: BudgetBackend | None = None,
        budget_backend: BudgetBackend | None = None,
        **kwargs,
    ) -> None:
        if not isinstance(config, BudgetAppConfig):
            raise ValueError("BudgetFundingService requires BudgetAppConfig")
        if backend is not None and budget_backend is not None:
            raise ValueError("backend and budget_backend are mutually exclusive")
        selected_backend = backend or budget_backend
        selected_policy = policy_service or BudgetPolicyService(config=config)
        super().__init__(
            config=config,
            storage_file=storage_file,
            rpc_transport=rpc_transport,
            policy_service=selected_policy,
            **kwargs,
        )
        self.backend = selected_backend
        self.budget_backend = selected_backend
        # The normal registry is intentionally left untouched.  This scoped
        # instance is the only registry consulted by this service.
        self.asset_registry = CanonicalAssetRegistry(
            {
                config.budget_network: {
                    "token_address": config.budget_token_address,
                    "token_symbol": config.budget_token_symbol,
                    "token_decimals": config.budget_token_decimals,
                }
            }
        )

    @property
    def budget_config(self) -> BudgetAppConfig:
        return self.config

    @staticmethod
    def _public_budget_row(row: Mapping[str, Any] | None) -> dict | None:
        """Return a reservation projection without executable attempt data.

        The ledger is the journal of record and retains the signed attempt
        needed for reconciliation. Reservation/API responses expose only
        non-executable status metadata for polling.
        """

        if row is None:
            return None
        public = dict(row)
        attempt = public.get("budget_attempt")
        if isinstance(attempt, Mapping):
            public["budget_attempt"] = {
                field_name: attempt[field_name]
                for field_name in _PUBLIC_BUDGET_ATTEMPT_FIELDS
                if field_name in attempt
            }
        else:
            public.pop("budget_attempt", None)
        return public

    def get_reservation(self, reservation_id: str) -> dict | None:
        with self.ledger.transaction() as tx:
            return self._public_budget_row(tx.get(reservation_id))

    def reserve_spending(self, request) -> dict:
        """Pin every new allowance-backed reservation to this trusted rail.

        ``payment_authorization`` is supplied later by a caller and therefore
        cannot select the settlement rail.  The budget service owns the rail
        at reservation creation; settlement requires the durable binding that
        is created for the Core grant before any transaction is prepared.
        """

        if getattr(request, "authorization_rail", None) != "native_allowance":
            raise ValueError("budget rail requires a native_allowance Core reservation")
        with self.ledger.transaction() as tx:
            existing = tx.by_purchase(request.purchase_id)
            if existing is not None and existing.get("settlement_rail") != _BUDGET_RAIL:
                raise ValueError("purchase is already owned by another settlement rail")
        row = super().reserve_spending(request)
        with self.ledger.transaction() as tx:
            current = tx.get(row["reservation_id"])
            if current is None:
                raise ValueError("reservation not found after budget reservation")
            existing_rail = current.get("settlement_rail")
            if existing_rail not in {None, _BUDGET_RAIL}:
                raise ValueError("reservation is already pinned to another settlement rail")
            if existing_rail is None and current.get("state") != "spending_reserved":
                raise ValueError("existing reservation is not a fresh budget reservation")
            if existing_rail != _BUDGET_RAIL:
                current = {**current, "settlement_rail": _BUDGET_RAIL}
                self._put_reservation(tx, current, tx_hash=current.get("tx_hash"))
            return current

    def _backend(self) -> BudgetBackend:
        backend = self.backend
        if backend is None:
            raise RuntimeError("budget execution backend is not configured")
        required = (
            "prepare",
            "broadcast",
            "verify",
            "pending_nonce",
            "allowance",
        )
        if any(not callable(getattr(backend, name, None)) for name in required):
            raise RuntimeError("budget execution backend is incomplete")
        for name in ("relayer_address", "execution_signer_address"):
            if not isinstance(getattr(backend, name, None), str):
                raise RuntimeError("budget execution backend signer scope is incomplete")
        return backend

    # ------------------------------------------------------------------
    # Budget policy/risk projection
    # ------------------------------------------------------------------
    def _require_current_policy_controls(
        self,
        scope,
        policy,
        *,
        action_type: str,
        user_id: str,
        agent_id: str,
        expected_metadata: dict,
    ) -> datetime | None:
        # Keep the base policy/action binding and destination controls, but do
        # not invoke its MistTrack-specific branch.  ``BudgetAppConfig`` may
        # advertise live funding while this testnet rail uses the explicit
        # first-party evidence below instead of fabricating a MistTrack result.
        destination = self._funding_scope_value(scope, "destination")
        try:
            policy_scope_matches = (
                policy is not None
                and getattr(policy, "approved", None) is True
                and getattr(policy, "policy_decision_id", None)
                == self._funding_scope_value(scope, "policy_decision_id")
                and getattr(policy, "action_id", None)
                == self._funding_scope_value(scope, "action_id")
                and getattr(policy, "action_type", None) == action_type
                and self._parse_amount(getattr(policy, "amount_usdc", ""))
                == self._parse_amount(self._funding_scope_value(scope, "amount_usdc"))
                and getattr(policy, "merchant_id", None)
                == self._funding_scope_value(scope, "merchant_id")
                and getattr(policy, "user_id", None) == user_id
                and getattr(policy, "agent_id", None) == agent_id
                and getattr(policy, "target_address", None) == destination
                and getattr(policy, "chain", None)
                == self._funding_scope_value(scope, "network")
                and getattr(policy, "metadata", None) == expected_metadata
            )
        except (TypeError, ValueError):
            policy_scope_matches = False
        if not policy_scope_matches:
            raise ValueError("marketplace policy scope mismatch")
        if destination in self.config.funding_destination_denylist:
            raise ValueError("destination is denied by current funding policy")
        if (
            self.config.funding_destination_allowlist
            and destination not in self.config.funding_destination_allowlist
        ):
            raise ValueError("destination is not allowed by current funding policy")
        return self._require_fresh_misttrack_assessment(scope, policy)

    def _require_fresh_misttrack_assessment(self, scope, policy) -> datetime:
        """Validate first-party testnet evidence; never impersonate MistTrack."""

        assessment = getattr(policy, "risk_assessment", None)
        config = self.budget_config
        network = self._funding_scope_value(scope, "network")
        destination = self._funding_scope_value(scope, "destination")
        resource = self._funding_scope_value(scope, "resource")
        merchant_id = self._funding_scope_value(scope, "merchant_id")
        token_value = self._funding_scope_value(scope, "token_address") or self._funding_scope_value(
            scope, "asset"
        )
        try:
            destination = canonicalize_evm_address(destination)
            token_value = canonicalize_evm_address(token_value)
        except (TypeError, ValueError):
            raise ValueError("testnet first-party risk assessment scope is invalid") from None

        if not isinstance(assessment, dict):
            raise ValueError("testnet first-party risk assessment is missing")
        expected = {
            "provider": BUDGET_RISK_PROVIDER,
            "provider_endpoint": BUDGET_RISK_ENDPOINT,
            "mapping_version": BUDGET_RISK_MAPPING_VERSION,
            "mode": "enforce",
            "enforced": True,
            "decision": "allow",
            "network": config.budget_network,
            "provider_network": config.budget_network,
            "subject": destination,
            "asset": config.budget_token_address,
            "token_address": config.budget_token_address,
            "token_symbol": config.budget_token_symbol,
            "coin": config.budget_token_symbol,
            "payee": destination,
            "merchant_id": merchant_id,
            "resource": resource,
            "test_only": True,
        }
        for field_name, expected_value in expected.items():
            if assessment.get(field_name) != expected_value:
                raise ValueError("testnet first-party risk assessment is not bound")
        canonical_scope = {
            "provider": BUDGET_RISK_PROVIDER,
            "endpoint": BUDGET_RISK_ENDPOINT,
            "network": config.budget_network,
            "token_address": config.budget_token_address,
            "payee": destination,
            "merchant_id": merchant_id,
            "resource": resource,
            "decision": "allow",
            "reasons": [],
        }
        expected_response_hash = hashlib.sha256(
            json.dumps(canonical_scope, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if assessment.get("response_sha256") != expected_response_hash:
            raise ValueError("testnet first-party risk assessment evidence hash is invalid")
        if destination not in config.budget_allowed_payees:
            raise ValueError("testnet payee is outside the configured allowlist")
        if merchant_id not in config.budget_allowed_merchant_ids:
            raise ValueError("testnet merchant is outside the configured allowlist")
        if resource not in config.budget_allowed_resources:
            raise ValueError("testnet resource is outside the configured allowlist")

        try:
            assessed_at = self._parse_risk_timestamp(assessment.get("assessed_at"))
            expires_at = self._parse_risk_timestamp(assessment.get("expires_at"))
        except ValueError:
            raise ValueError("testnet first-party risk assessment timestamps are invalid") from None
        now = self._risk_now()
        if (
            assessed_at > now
            or expires_at <= assessed_at
            or now >= expires_at
            or (now - assessed_at).total_seconds() > config.risk_max_age_seconds
        ):
            raise ValueError("testnet first-party risk assessment is stale")
        return expires_at

    # ------------------------------------------------------------------
    # Durable Core ↔ chain grant binding
    # ------------------------------------------------------------------
    def get_budget_binding(
        self,
        *,
        binding_id: str | None = None,
        spending_grant_id: str | None = None,
    ) -> BudgetBinding | None:
        if (binding_id is None) == (spending_grant_id is None):
            raise ValueError("provide exactly one budget binding lookup key")
        with self.ledger.transaction() as tx:
            records = tx.list(_BUDGET_BINDING_RECORD)
        for payload in records:
            if not isinstance(payload, Mapping):
                continue
            if binding_id is not None and payload.get("binding_id") != binding_id:
                continue
            if spending_grant_id is not None and payload.get("spending_grant_id") != spending_grant_id:
                continue
            return BudgetBinding.from_record(payload)
        return None

    def bind_budget_grant(
        self,
        *,
        spending_grant_id: str,
        wallet_identity_id: str,
        grant: Any,
        owner_signature: str | bytes,
        agent_id: str | None = None,
        network: str | None = None,
        executor_contract: str | None = None,
        token_address: str | None = None,
        payee: str | None = None,
        execution_signer: str | None = None,
    ) -> BudgetBinding:
        config = self.budget_config
        network = network or config.budget_network
        executor_contract = executor_contract or config.budget_contract_address
        token_address = token_address or config.budget_token_address
        payee = payee or config.budget_payee_address
        spending_grant_id = str(spending_grant_id)
        wallet_identity_id = str(wallet_identity_id)
        model = _grant_model(grant)
        payload = _canonical_grant_payload(model)
        binding_id = binding_id_for(spending_grant_id)
        now = self._as_utc_datetime(self._utc_now())

        with self.ledger.transaction() as tx:
            identity = tx.s.get(WalletIdentityRow, wallet_identity_id)
            core_grant = tx.s.get(SpendingGrantRow, spending_grant_id)
            if identity is None or core_grant is None:
                raise ValueError("Core wallet identity or spending grant is unavailable")
            if identity.status != "active":
                raise ValueError("active wallet identity required for budget binding")
            if core_grant.wallet_identity_id != wallet_identity_id:
                raise ValueError("budget binding identity does not match Core grant")
            if core_grant.status != "active":
                raise ValueError("active Core spending grant required for budget binding")
            if agent_id is not None and agent_id != core_grant.agent_id:
                raise ValueError("budget binding agent identity does not match Core grant")
            agent_id = core_grant.agent_id
            owner = canonicalize_evm_address(identity.wallet_address)
            executor_contract = canonicalize_evm_address(executor_contract)
            token_address = canonicalize_evm_address(token_address)
            payee = canonicalize_evm_address(payee)
            execution_signer = execution_signer or getattr(
                self.backend, "execution_signer_address", None
            )
            execution_signer = canonicalize_evm_address(execution_signer)
            backend_signer = getattr(self.backend, "execution_signer_address", None)
            if backend_signer is not None:
                if canonicalize_evm_address(backend_signer) != execution_signer:
                    raise ValueError("budget SpendGrant execution signer does not match backend")
            if network != config.budget_network or token_address != config.budget_token_address:
                raise ValueError("budget binding network or token is outside the configured profile")
            if executor_contract != config.budget_contract_address or payee != config.budget_payee_address:
                raise ValueError("budget binding contract or payee is outside the configured profile")
            if payee not in config.budget_allowed_payees:
                raise ValueError("budget binding payee is outside the test allowlist")

            def atomic(value: Any, *, field_name: str) -> int:
                try:
                    decimal_value = Decimal(str(value)) * (10**config.budget_token_decimals)
                except (InvalidOperation, ValueError) as exc:
                    raise ValueError(f"Core grant {field_name} is invalid") from exc
                if not decimal_value.is_finite() or decimal_value != decimal_value.to_integral_value():
                    raise ValueError(f"Core grant {field_name} cannot be represented exactly")
                result = int(decimal_value)
                if result <= 0 or result > 2**256 - 1:
                    raise ValueError(f"Core grant {field_name} is outside uint256")
                return result

            expected_max_total = atomic(core_grant.max_amount_usdc, field_name="max_amount_usdc")
            expected_max_payment = atomic(
                core_grant.per_transaction_limit_usdc,
                field_name="per_transaction_limit_usdc",
            )
            expected_after = int(self._as_utc_datetime(core_grant.starts_at).timestamp())
            expected_until = int(self._as_utc_datetime(core_grant.expires_at).timestamp())
            fields = {
                "grantId": _grant_value(model, "grant_id", "grantId"),
                "owner": _grant_value(model, "owner", "owner"),
                "agentScope": _grant_value(model, "agent_scope", "agentScope"),
                "token": _grant_value(model, "token", "token"),
                "payee": _grant_value(model, "payee", "payee"),
                "maxPerPayment": _grant_value(model, "max_per_payment", "maxPerPayment"),
                "maxTotal": _grant_value(model, "max_total", "maxTotal"),
                "validAfter": _grant_value(model, "valid_after", "validAfter"),
                "validUntil": _grant_value(model, "valid_until", "validUntil"),
                "executionSigner": _grant_value(model, "execution_signer", "executionSigner"),
            }
            try:
                grant_id = canonical_bytes32(fields["grantId"], field_name="grantId")
                grant_owner = canonicalize_evm_address(fields["owner"])
                grant_scope = canonical_bytes32(fields["agentScope"], field_name="agentScope")
                grant_token = canonicalize_evm_address(fields["token"])
                grant_payee = canonicalize_evm_address(fields["payee"])
                grant_signer = canonicalize_evm_address(fields["executionSigner"])
                max_payment = int(str(fields["maxPerPayment"]), 10)
                max_total = int(str(fields["maxTotal"]), 10)
                valid_after = int(str(fields["validAfter"]), 10)
                valid_until = int(str(fields["validUntil"]), 10)
            except (TypeError, ValueError) as exc:
                raise ValueError("budget SpendGrant fields are invalid") from exc
            if grant_owner != owner:
                raise ValueError("budget SpendGrant owner does not match wallet identity")
            from services.funding_service.budget_binding import derive_agent_scope

            if grant_scope != derive_agent_scope(agent_id, spending_grant_id):
                raise ValueError("budget SpendGrant agent scope does not commit to Core grant")
            if (
                grant_token != token_address
                or grant_payee != payee
                or grant_signer != execution_signer
                or max_payment != expected_max_payment
                or max_total != expected_max_total
                or valid_after != expected_after
                or valid_until != expected_until
            ):
                raise ValueError("budget SpendGrant does not exactly match Core grant scope")
            if (
                network not in (core_grant.network_scopes or [])
                or token_address.lower() not in {str(value).lower() for value in core_grant.asset_scopes}
            ):
                raise ValueError("Core grant does not authorize the budget network or token")
            if max_payment > max_total or max_total > 2**256 - 1:
                raise ValueError("budget SpendGrant limits exceed chain bounds")

            signature = verify_owner_signature(
                model,
                owner_signature,
                chain_id=config.budget_chain_id,
                executor_contract=executor_contract,
                owner=owner,
            )
            digest = protocol_grant_hash(model, config.budget_chain_id, executor_contract)
            proposed = BudgetBinding(
                binding_id=binding_id,
                spending_grant_id=spending_grant_id,
                wallet_identity_id=wallet_identity_id,
                user_id=core_grant.user_id,
                agent_id=agent_id,
                owner=owner,
                agent_scope=grant_scope,
                network=network,
                chain_id=config.budget_chain_id,
                executor_contract=executor_contract,
                token=token_address,
                token_symbol=config.budget_token_symbol,
                token_decimals=config.budget_token_decimals,
                payee=payee,
                max_per_payment=max_payment,
                max_total=max_total,
                valid_after=valid_after,
                valid_until=valid_until,
                execution_signer=execution_signer,
                grant_id=grant_id,
                grant_hash=digest,
                owner_signature=signature,
                grant_payload=payload,
                created_at=self._format_time(now),
            )
            for record in tx.list(_BUDGET_BINDING_RECORD):
                if not isinstance(record, Mapping):
                    continue
                existing = BudgetBinding.from_record(record)
                same_chain_identity = (
                    existing.owner == proposed.owner
                    and existing.grant_id == proposed.grant_id
                    and existing.chain_id == proposed.chain_id
                    and existing.executor_contract == proposed.executor_contract
                )
                if existing.spending_grant_id != spending_grant_id and same_chain_identity:
                    raise ValueError(
                        "chain grant identity is already bound to another Core spending grant"
                    )
                if existing.spending_grant_id != spending_grant_id:
                    continue
                existing_values = existing.to_dict()
                proposed_values = proposed.to_dict()
                # ``created_at`` records the first durable bind and is not part
                # of the immutable grant scope.  Replaying an identical
                # owner-signed grant after a clock tick must return that
                # record rather than manufacture a conflicting binding.
                existing_values.pop("created_at", None)
                proposed_values.pop("created_at", None)
                if existing_values != proposed_values:
                    raise ValueError("Core spending grant already has a different budget binding")
                return existing
            tx.put(_BUDGET_BINDING_RECORD, binding_id, proposed.to_dict())
            return proposed

    # Explicit aliases keep composition code readable while retaining one
    # implementation and one durable identity.
    create_budget_binding = bind_budget_grant
    bind_spend_grant = bind_budget_grant

    def _binding_for_row(self, tx, row: Mapping[str, Any]) -> BudgetBinding:
        require_unified_reservation(dict(row))
        requested_id = row.get("budget_binding_id")
        records = tx.list(_BUDGET_BINDING_RECORD)
        selected = None
        for record in records:
            if not isinstance(record, Mapping):
                continue
            if requested_id and record.get("binding_id") != requested_id:
                continue
            if record.get("spending_grant_id") != row.get("spending_grant_id"):
                continue
            selected = record
            break
        if selected is None:
            raise ValueError("budget binding is unavailable")
        binding = BudgetBinding.from_record(selected)
        if binding.status != "active":
            raise ValueError("budget binding is not active")
        if requested_id and binding.binding_id != requested_id:
            raise ValueError("budget binding identity changed")
        binding.require_row_scope(row)
        if binding.network != self.budget_config.budget_network:
            raise ValueError("budget binding network is outside profile")
        if protocol_grant_hash(
            binding.grant, binding.chain_id, binding.executor_contract
        ) != binding.grant_hash:
            raise ValueError("budget binding grant digest changed")
        grant = binding.grant
        grant_scope = {
            "grant_id": canonical_bytes32(
                _grant_value(grant, "grant_id", "grantId"), field_name="grantId"
            ),
            "owner": canonicalize_evm_address(_grant_value(grant, "owner", "owner")),
            "agent_scope": canonical_bytes32(
                _grant_value(grant, "agent_scope", "agentScope"), field_name="agentScope"
            ),
            "token": canonicalize_evm_address(_grant_value(grant, "token", "token")),
            "payee": canonicalize_evm_address(_grant_value(grant, "payee", "payee")),
            "max_per_payment": int(
                _grant_value(grant, "max_per_payment", "maxPerPayment")
            ),
            "max_total": int(_grant_value(grant, "max_total", "maxTotal")),
            "valid_after": int(_grant_value(grant, "valid_after", "validAfter")),
            "valid_until": int(_grant_value(grant, "valid_until", "validUntil")),
            "execution_signer": canonicalize_evm_address(
                _grant_value(grant, "execution_signer", "executionSigner")
            ),
        }
        for field_name, expected_value in grant_scope.items():
            if getattr(binding, field_name) != expected_value:
                raise ValueError("budget binding grant scope changed")
        verify_owner_signature(
            binding.grant,
            binding.owner_signature,
            chain_id=binding.chain_id,
            executor_contract=binding.executor_contract,
            owner=binding.owner,
        )
        identity, core_grant, _allowance = tx.unified_authorization_scope(dict(row))
        if identity.wallet_identity_id != binding.wallet_identity_id:
            raise ValueError("budget binding identity changed")
        if canonicalize_evm_address(identity.wallet_address) != binding.owner:
            raise ValueError("budget binding owner changed")
        if _allowance is None or canonicalize_evm_address(_allowance.spender_address) != binding.executor_contract:
            raise ValueError("Core allowance spender does not match budget executor")
        if core_grant.spending_grant_id != binding.spending_grant_id:
            raise ValueError("budget binding Core grant changed")
        if core_grant.agent_id != binding.agent_id or core_grant.user_id != binding.user_id:
            raise ValueError("budget binding Core actor changed")
        expected_total = int(Decimal(core_grant.max_amount_usdc) * 10**binding.token_decimals)
        expected_payment = int(
            Decimal(core_grant.per_transaction_limit_usdc) * 10**binding.token_decimals
        )
        if expected_total != binding.max_total or expected_payment != binding.max_per_payment:
            raise ValueError("budget binding Core limits changed")
        if int(self._as_utc_datetime(core_grant.starts_at).timestamp()) != binding.valid_after:
            raise ValueError("budget binding Core start changed")
        if int(self._as_utc_datetime(core_grant.expires_at).timestamp()) != binding.valid_until:
            raise ValueError("budget binding Core expiry changed")
        return binding

    # ------------------------------------------------------------------
    # Budget settlement and recovery
    # ------------------------------------------------------------------
    @staticmethod
    def _budget_requested(request: SettleSpendingReservationRequest) -> bool:
        authorization = request.payment_authorization
        if not isinstance(authorization, Mapping):
            return False
        return authorization.get("rail") == _BUDGET_RAIL or authorization.get(
            "settlement_rail"
        ) == _BUDGET_RAIL or authorization.get("budget_binding_id") is not None

    def settle_reservation(
        self,
        reservation_id: str,
        request: SettleSpendingReservationRequest,
    ) -> dict:
        authorization = request.payment_authorization
        if not isinstance(authorization, Mapping):
            authorization = {}
        with self.ledger.transaction() as tx:
            row = tx.get(reservation_id)
            if row is None:
                raise ValueError("reservation not found")
            if row.get("settlement_rail") != _BUDGET_RAIL:
                raise ValueError(
                    "BudgetFundingService requires a budget_contract settlement rail"
                )
            for field_name in ("rail", "settlement_rail"):
                requested_rail = authorization.get(field_name)
                if requested_rail is not None and requested_rail != _BUDGET_RAIL:
                    raise ValueError(
                        "settlement rail is owned by the trusted budget service"
                    )
        with self.ledger.transaction() as tx:
            row = tx.get(reservation_id)
            if row is None:
                raise ValueError("reservation not found")
            require_unified_reservation(row)
            self._require_canonical_asset_pair(row)
            if row.get("state") in {"settled", "finalized"}:
                return self._public_budget_row(row)
            if row.get("settlement_rail") == _BUDGET_RAIL and row.get("state") == "payment_submitted":
                requested_binding_id = authorization.get("budget_binding_id")
                if (
                    requested_binding_id is not None
                    and requested_binding_id != row.get("budget_binding_id")
                ):
                    raise ValueError("a different budget binding is pending")
                pending = True
            else:
                pending = False
                if row.get("state") != "spending_reserved":
                    raise ValueError(f"reservation is {row.get('state')}")
                identity, _grant, _allowance = tx.validate_unified_lifecycle(
                    row, now=self._utc_now()
                )
                self._require_reservation_policy_controls(row)
                binding = self._binding_for_row(tx, row)
                owner = canonicalize_evm_address(identity.wallet_address)
                observed_allowance = self._checked_budget_allowance(owner)
                if observed_allowance < int(row["amount_atomic"]):
                    raise ValueError("budget token allowance is insufficient before submission")
                backend = self._backend()
                chain_nonce = backend.pending_nonce()
                if type(chain_nonce) is not int or chain_nonce < 0 or chain_nonce >= 2**64:
                    raise ValueError("budget backend pending nonce is invalid")
                relayer = canonicalize_evm_address(backend.relayer_address)
                # Existing allocator is intentionally reused.  The row's
                # authorization rail remains the existing Core allowance rail;
                # only the settlement rail is budget_contract.
                nonce_row = {**row, "authorization_rail": "native_allowance"}
                nonce = tx.allocate_relayer_nonce(
                    nonce_row,
                    network=row["network"],
                    relayer_address=relayer,
                    chain_pending_nonce=chain_nonce,
                    now=self._utc_now(),
                )
                attempt = _json_value(backend.prepare(row, binding, nonce))
                if not isinstance(attempt, dict):
                    raise ValueError("budget backend prepare result is invalid")
                attempt = self._validate_prepared_attempt(attempt, row, binding, relayer, nonce)
                self._require_reservation_policy_controls(row)
                tx.validate_unified_lifecycle(row, now=self._utc_now())
                row = {
                    **row,
                    "state": "payment_submitted",
                    "settlement_rail": _BUDGET_RAIL,
                    "budget_binding_id": binding.binding_id,
                    "budget_attempt": attempt,
                    # The durable binding, rather than caller-supplied rail
                    # hints, defines the idempotency identity for retries.
                    "payment_authorization_hash": _authorization_hash(
                        {
                            "rail": _BUDGET_RAIL,
                            "budget_binding_id": binding.binding_id,
                        }
                    ),
                    "tx_hash": attempt["tx_hash"],
                    "settlement_sender": relayer,
                    "settlement_nonce": nonce,
                    "settlement_transaction": attempt.get("transaction", {}),
                    "receipt_id": f"fund_receipt_{reservation_id}",
                    "reconciliation_status": "pending",
                    "next_action": "reconcile_payment",
                    "reconciliation_attempts": 0,
                    "reconciliation_started_at": self._format_time(self._utc_now()),
                    "last_reconciliation_at": None,
                    "last_reconciliation_error": None,
                    "manual_review_reason": None,
                    "manual_review_required_at": None,
                    "risk_prebroadcast_state": "pending",
                    "risk_prebroadcast_blocked": False,
                }
                self._put_reservation(tx, row, tx_hash=attempt["tx_hash"])

        if pending:
            # A durable attempt may be waiting for its first broadcast, have a
            # committed broadcast claim, or already have an ambiguous outcome.
            # Recovery always uses the same attempt and never prepares a new
            # transaction or changes the settlement rail.
            return self._recover_budget_attempt(reservation_id)

        # The transaction attempt is durable before this call.  The final Core
        # lifecycle/policy check and the durable broadcast claim happen in a
        # ledger transaction; the external callback runs after that transaction
        # closes so a network call never holds the funding lock.
        self._broadcast_budget_attempt(reservation_id)
        return self.reconcile_reservation(reservation_id)

    def _recover_budget_attempt(self, reservation_id: str) -> dict:
        with self.ledger.transaction() as tx:
            row = tx.get(reservation_id)
            if row is None:
                raise ValueError("reservation not found")
            if row.get("state") != "payment_submitted":
                return self._public_budget_row(row)
            broadcast_state = row.get("risk_prebroadcast_state")
        if broadcast_state in {"pending", "ready"}:
            self._broadcast_budget_attempt(reservation_id)
        # Leave any ledger transaction before reconciliation.  SQLite uses an
        # immediate write lock and nested acquisition would deadlock.
        return self.reconcile_reservation(reservation_id)

    def _checked_budget_allowance(self, owner: str) -> int:
        value = self._backend().allowance(owner)
        if type(value) is not int or value < 0 or value > 2**256 - 1:
            raise ValueError("budget backend allowance is invalid")
        return value

    def _validate_prepared_attempt(
        self,
        attempt: dict,
        row: Mapping[str, Any],
        binding: BudgetBinding,
        relayer: str,
        nonce: int,
    ) -> dict:
        return validate_signed_budget_attempt(
            attempt,
            row,
            binding,
            relayer=relayer,
            nonce=nonce,
            now_timestamp=int(self._as_utc_datetime(self._utc_now()).timestamp()),
        )

    def _authorize_budget_broadcast(self, reservation_id: str) -> dict:
        with self.ledger.transaction() as tx:
            row = tx.get(reservation_id)
            if row is None:
                raise ValueError("reservation not found")
            if row.get("state") != "payment_submitted" or row.get("settlement_rail") != _BUDGET_RAIL:
                raise ValueError("budget transaction is not pending")
            binding = self._binding_for_row(tx, row)
            # Revocation or expiry after prepare stops a new broadcast.  A
            # previously broadcast attempt remains recoverable by reconcile.
            tx.validate_unified_lifecycle(row, now=self._utc_now())
            self._require_reservation_policy_controls(row)
            if row.get("risk_prebroadcast_state") != "pending":
                raise ValueError("budget transaction is not awaiting broadcast authorization")
            authorized = {**row, "risk_prebroadcast_state": "ready"}
            self._put_reservation(tx, authorized, tx_hash=row.get("tx_hash"))
            return authorized

    def _broadcast_budget_attempt(self, reservation_id: str) -> dict:
        """Claim a durable attempt, then invoke the broadcaster outside the DB tx.

        A submitted raw transaction is immutable.  The pending/ready claim is
        committed before the callback; if a process dies before that claim,
        recovery may re-run the same attempt after fresh Core checks.  Once a
        broadcasting claim is committed, recovery is verify-only because the
        callback outcome is unknown and a post-revocation send is forbidden.
        """

        with self.ledger.transaction() as tx:
            row = tx.get(reservation_id)
            if row is None:
                raise ValueError("reservation not found")
            if row.get("state") != "payment_submitted" or row.get("settlement_rail") != _BUDGET_RAIL:
                raise ValueError("budget transaction is not pending")
            broadcast_state = row.get("risk_prebroadcast_state")
            if broadcast_state in {"broadcasted", "ambiguous", "broadcasting"}:
                return row
            if broadcast_state not in {"pending", "ready"}:
                raise ValueError("budget transaction is no longer awaiting broadcast authorization")
            binding = self._binding_for_row(tx, row)
            attempt = row.get("budget_attempt")
            if not isinstance(attempt, dict):
                raise ValueError("budget transaction attempt is unavailable")
            # The first durable claim is the final pre-broadcast control point.
            # A committed broadcasting claim is verify-only after a crash; it
            # must not create a post-revocation network send.
            tx.validate_unified_lifecycle(row, now=self._utc_now())
            if self._checked_budget_allowance(binding.owner) < int(row["amount_atomic"]):
                raise ValueError("budget token allowance is insufficient before broadcast")
            self._require_reservation_policy_controls(row)
            prepared = self._validate_prepared_attempt(
                attempt,
                row,
                binding,
                canonicalize_evm_address(self._backend().relayer_address),
                int(row.get("settlement_nonce")),
            )
            if prepared["tx_hash"] != canonicalize_transaction_hash(row.get("tx_hash")):
                raise ValueError("durable budget attempt hash does not match reservation")
            # Commit the claim before entering any external transport call.
            broadcasting = {
                **row,
                "risk_prebroadcast_state": "broadcasting",
                "budget_broadcast_claimed_at": self._format_time(self._utc_now()),
            }
            self._put_reservation(tx, broadcasting, tx_hash=row.get("tx_hash"))

        # Never hold the ledger/global funding lock while calling the network.
        try:
            submitted = canonicalize_transaction_hash(self._backend().broadcast(prepared))
        except Exception as exc:
            with self.ledger.transaction() as tx:
                current = tx.get(reservation_id)
                if current is None:
                    raise ValueError("reservation not found")
                if (
                    current.get("state") != "payment_submitted"
                    or current.get("tx_hash") != row.get("tx_hash")
                ):
                    return current
                ambiguous = {
                    **current,
                    "risk_prebroadcast_state": "ambiguous",
                    "last_reconciliation_error": (
                        f"budget submission status is ambiguous: {exc}"
                    ),
                }
                self._put_reservation(tx, ambiguous, tx_hash=current.get("tx_hash"))
                return ambiguous
        with self.ledger.transaction() as tx:
            current = tx.get(reservation_id)
            if current is None:
                raise ValueError("reservation not found")
            if (
                current.get("state") != "payment_submitted"
                or current.get("tx_hash") != row.get("tx_hash")
            ):
                return current
            if submitted != row.get("tx_hash"):
                ambiguous = {
                    **current,
                    "risk_prebroadcast_state": "ambiguous",
                    "last_reconciliation_error": (
                        "budget backend returned a mismatched transaction hash"
                    ),
                }
                self._put_reservation(tx, ambiguous, tx_hash=current.get("tx_hash"))
                return ambiguous
            broadcasted = {
                **current,
                "risk_prebroadcast_state": "broadcasted",
                "budget_broadcasted_at": self._format_time(self._utc_now()),
            }
            self._put_reservation(tx, broadcasted, tx_hash=current.get("tx_hash"))
            return broadcasted

    def _save_budget_error(self, reservation_id: str, error: str) -> None:
        with self.ledger.transaction() as tx:
            row = tx.get(reservation_id)
            if row is None or row.get("state") != "payment_submitted":
                return
            updated = {
                **row,
                "last_reconciliation_error": f"budget submission status is ambiguous: {error}",
                "reconciliation_status": "pending",
                "next_action": "reconcile_payment",
            }
            self._put_reservation(tx, updated, tx_hash=row.get("tx_hash"))

    def reconcile_reservation(self, reservation_id: str, *, operator_reconcile: bool = False) -> dict:
        with self.ledger.transaction() as tx:
            row = tx.get(reservation_id)
            if row is None:
                raise ValueError("reservation not found")
            is_budget = row.get("settlement_rail") == _BUDGET_RAIL
        if not is_budget:
            return super().reconcile_reservation(
                reservation_id, operator_reconcile=operator_reconcile
            )
        return self._public_budget_row(
            self._reconcile_budget_reservation(
                reservation_id, operator_reconcile=operator_reconcile
            )
        )

    def _reconcile_budget_reservation(
        self, reservation_id: str, *, operator_reconcile: bool = False
    ) -> dict:
        with self.ledger.transaction() as tx:
            row = tx.get(reservation_id)
            if row is None:
                raise ValueError("reservation not found")
            if row.get("state") in {"settled", "finalized", "unpaid_terminal"}:
                return row
            if row.get("state") != "payment_submitted":
                return row
            if row.get("reconciliation_status") == "manual_review_required" and not operator_reconcile:
                return row
            now = self._utc_now()
            if not operator_reconcile:
                reason = self._reconciliation_limit_reason(row, now)
                if reason:
                    updated = self._manual_review_reservation(row, reason, now=now)
                    self._put_reservation(tx, updated, tx_hash=row.get("tx_hash"))
                    return updated
                row = {
                    **row,
                    "reconciliation_attempts": int(row.get("reconciliation_attempts") or 0) + 1,
                    "reconciliation_started_at": row.get("reconciliation_started_at")
                    or self._format_time(now),
                    "last_reconciliation_at": self._format_time(now),
                }
            else:
                row = {
                    **row,
                    "operator_reconciliation_attempts": int(
                        row.get("operator_reconciliation_attempts") or 0
                    )
                    + 1,
                    "last_operator_reconciliation_at": self._format_time(now),
                }
            binding = self._binding_for_row(tx, row)
            attempt = row.get("budget_attempt")
            if not isinstance(attempt, Mapping):
                raise ValueError("budget transaction attempt is unavailable")
            self._put_reservation(tx, row, tx_hash=row.get("tx_hash"))

        try:
            evidence = self._backend().verify(dict(row), binding, dict(attempt))
        except Exception as exc:
            return self._pending_budget(row, f"budget verification failed: {exc}")
        if evidence is None or not isinstance(evidence, Mapping):
            return self._pending_budget(row, "budget transaction remains pending")
        evidence = _json_value(evidence)
        status = str(evidence.get("status", "")).lower()
        if status == "reverted":
            try:
                self._validate_budget_revert_evidence(row, binding, evidence)
            except (TypeError, ValueError) as exc:
                return self._pending_budget(
                    row,
                    f"budget revert evidence rejected: {exc}",
                    evidence=evidence,
                )
            return self._mark_budget_unpaid(row, evidence)
        if evidence.get("reverted") is True or status in {"failed", "unpaid_terminal"}:
            return self._pending_budget(
                row,
                "budget transaction does not contain an independently verified final revert",
                evidence=evidence,
            )
        if evidence.get("verified") is not True or status != "verified":
            return self._pending_budget(row, "budget transaction remains pending", evidence=evidence)
        try:
            self._validate_budget_evidence(row, binding, evidence)
        except (TypeError, ValueError) as exc:
            # A malformed or scope-disagreeing watcher result is unknown
            # payment state.  Keep the Core reservation and durable attempt;
            # never turn an invalid observation into a successful spend.
            return self._pending_budget(
                row,
                f"budget watcher evidence rejected: {exc}",
                evidence=evidence,
            )
        with self.ledger.transaction() as tx:
            current = tx.get(reservation_id)
            if current is None:
                raise ValueError("reservation not found")
            if current.get("state") != "payment_submitted" or current.get("tx_hash") != row.get("tx_hash"):
                return current
            updated = {**current, "budget_watcher_evidence": evidence}
            self._put_reservation(tx, updated, tx_hash=current.get("tx_hash"))
        # This helper performs the existing receipt creation and atomic Core
        # used/reserved ledger transition.  Evidence has already been checked.
        return self._complete_native_reservation(updated)

    def _validate_budget_evidence(
        self, row: Mapping[str, Any], binding: BudgetBinding, evidence: Mapping[str, Any]
    ) -> None:
        self._validate_budget_evidence_common(
            row,
            binding,
            evidence,
            expected_status="verified",
            expected_receipt_status=1,
        )

    def _validate_budget_revert_evidence(
        self, row: Mapping[str, Any], binding: BudgetBinding, evidence: Mapping[str, Any]
    ) -> None:
        self._validate_budget_evidence_common(
            row,
            binding,
            evidence,
            expected_status="reverted",
            expected_receipt_status=0,
        )

    def _validate_budget_evidence_common(
        self,
        row: Mapping[str, Any],
        binding: BudgetBinding,
        evidence: Mapping[str, Any],
        *,
        expected_status: str,
        expected_receipt_status: int,
    ) -> None:
        if evidence.get("verified") is not True or evidence.get("status") != expected_status:
            raise ValueError("budget watcher evidence is not independently verified")
        transaction_hash = evidence.get("transaction_hash", evidence.get("tx_hash"))
        required = (
            "chain_id",
            "executor",
            "grant_hash",
            "owner",
            "token",
            "payee",
            "amount_atomic",
            "purchase_id",
            "quote_hash",
            "block_number",
            "block_hash",
            "finality_kind",
            "finality_block_number",
            "finality_block_hash",
            "receipt_status",
            "two_rpc_verified",
        )
        if transaction_hash is None or any(field_name not in evidence for field_name in required):
            raise ValueError("budget watcher evidence is incomplete")
        expected = {
            "transaction_hash": row.get("tx_hash"),
            "grant_hash": binding.grant_hash,
            "owner": binding.owner,
            "purchase_id": row.get("purchase_id"),
            "quote_hash": row.get("quote_hash"),
            "amount_atomic": row.get("amount_atomic"),
            "payee": binding.payee,
            "token": binding.token,
            "executor": binding.executor_contract,
            "chain_id": binding.chain_id,
        }
        for field_name, expected_value in expected.items():
            actual = transaction_hash if field_name == "transaction_hash" else evidence.get(field_name)
            if field_name == "transaction_hash":
                actual = canonicalize_transaction_hash(actual)
            elif field_name in {"owner", "payee", "token", "executor"}:
                actual = canonicalize_evm_address(actual)
            elif field_name in {"grant_hash", "quote_hash"}:
                actual = canonical_bytes32(actual, field_name=field_name)
            if str(actual) != str(expected_value):
                raise ValueError(f"budget watcher evidence {field_name} does not match binding")
        try:
            if type(evidence["receipt_status"]) is not int or evidence["receipt_status"] != expected_receipt_status:
                raise ValueError
            if evidence["two_rpc_verified"] is not True:
                raise ValueError
            expected_finality_kind = (
                "monad_verified"
                if self.budget_config.budget_mode == "monad_testnet"
                else "local"
            )
            if evidence["finality_kind"] != expected_finality_kind:
                raise ValueError
            block_number = evidence["block_number"]
            if type(block_number) is not int or block_number < 0:
                raise ValueError
            canonical_bytes32(evidence["block_hash"], field_name="block_hash")
            finality_block_number = evidence["finality_block_number"]
            if type(finality_block_number) is not int or finality_block_number < block_number:
                raise ValueError
            finality_block_hash = canonical_bytes32(
                evidence["finality_block_hash"], field_name="finality_block_hash"
            )
            if finality_block_number == block_number and finality_block_hash != evidence["block_hash"]:
                raise ValueError
        except (TypeError, ValueError, KeyError):
            raise ValueError("budget watcher finality evidence is invalid") from None

    def _pending_budget(
        self, row: Mapping[str, Any], error: str, *, evidence: Mapping[str, Any] | None = None
    ) -> dict:
        with self.ledger.transaction() as tx:
            current = tx.get(row["reservation_id"])
            if current is None:
                raise ValueError("reservation not found")
            if current.get("state") != "payment_submitted" or current.get("tx_hash") != row.get("tx_hash"):
                return current
            updated = {
                **current,
                "reconciliation_status": "pending",
                "next_action": "reconcile_payment",
                "last_reconciliation_error": error,
            }
            if evidence is not None:
                updated["budget_watcher_evidence"] = dict(evidence)
            self._put_reservation(tx, updated, tx_hash=current.get("tx_hash"))
            return updated

    def _mark_budget_unpaid(self, row: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict:
        with self.ledger.transaction() as tx:
            current = tx.get(row["reservation_id"])
            if current is None:
                raise ValueError("reservation not found")
            if current.get("state") in {"settled", "finalized"}:
                return current
            if current.get("state") != "payment_submitted":
                return current
            if current.get("budget_accounting_state") != "reserved":
                raise ValueError("budget reservation is not reserved for revert recovery")
            binding = self._binding_for_row(tx, current)
            self._validate_budget_revert_evidence(current, binding, evidence)
            updated = {
                **current,
                "state": "unpaid_terminal",
                "reconciliation_status": "manual_review_required",
                "next_action": "operator_reconcile",
                "manual_review_reason": (
                    "budget transaction was independently proven reverted; reservation released"
                ),
                "manual_review_required_at": current.get("manual_review_required_at")
                or self._format_time(self._utc_now()),
                "budget_watcher_evidence": dict(evidence),
                "budget_revert_proof": {
                    "validated": True,
                    "transaction_hash": canonicalize_transaction_hash(
                        evidence.get("transaction_hash", evidence.get("tx_hash"))
                    ),
                    "receipt_status": 0,
                    "finality_kind": evidence["finality_kind"],
                    "finality_block_number": evidence["finality_block_number"],
                    "finality_block_hash": evidence["finality_block_hash"],
                    "two_rpc_verified": True,
                },
            }
            released = tx.release_unified_budget(updated, now=self._utc_now())
            updated = {
                **released,
                "state": "unpaid_terminal",
                "release_reason": "verified_final_revert",
                "budget_revert_released_at": self._format_time(self._utc_now()),
            }
            self._put_reservation(tx, updated, tx_hash=current.get("tx_hash"))
            return updated

    def release_reservation(
        self,
        reservation_id: str,
        request: ReleaseSpendingReservationRequest,
    ) -> dict:
        """Release a budget reservation only after Core validated final revert proof."""

        delegate = False
        with self.ledger.transaction() as tx:
            row = tx.get(reservation_id)
            if row is None:
                raise ValueError("reservation not found")
            if row.get("settlement_rail") != _BUDGET_RAIL:
                delegate = True
            elif row.get("state") == "released":
                return self._public_budget_row(row)
            elif row.get("state") != "unpaid_terminal":
                delegate = True
            else:
                require_unified_reservation(row)
                proof = row.get("budget_revert_proof")
                expected_finality_kind = (
                    "monad_verified"
                    if self.budget_config.budget_mode == "monad_testnet"
                    else "local"
                )
                if not isinstance(proof, Mapping) or proof.get("validated") is not True:
                    raise ValueError("budget unpaid terminal release requires validated revert proof")
                if (
                    canonicalize_transaction_hash(proof.get("transaction_hash"))
                    != canonicalize_transaction_hash(row.get("tx_hash"))
                    or proof.get("receipt_status") != 0
                    or proof.get("two_rpc_verified") is not True
                    or proof.get("finality_kind") != expected_finality_kind
                    or type(proof.get("finality_block_number")) is not int
                    or proof.get("finality_block_number") < 0
                ):
                    raise ValueError("budget unpaid terminal release proof is invalid")
                canonical_bytes32(
                    proof.get("finality_block_hash"), field_name="finality_block_hash"
                )
                if row.get("budget_accounting_state") == "released":
                    return self._public_budget_row(row)
                if row.get("budget_accounting_state") != "reserved":
                    raise ValueError("reservation budget is not releasable")
                released = tx.release_unified_budget(row, now=self._utc_now())
                released = {
                    **released,
                    "state": "released",
                    "release_reason": request.reason,
                    "budget_revert_released_at": self._format_time(self._utc_now()),
                }
                self._put_reservation(tx, released, tx_hash=row.get("tx_hash"))
                return self._public_budget_row(released)
        if delegate:
            return self._public_budget_row(super().release_reservation(reservation_id, request))
        raise ValueError("budget reservation release is unavailable")

    def get_funding_readiness(self) -> dict:
        config = self.budget_config
        missing: list[str] = []
        backend = self.backend
        if backend is None:
            missing.append("BUDGET_BACKEND")
        else:
            for field_name in ("relayer_address", "execution_signer_address"):
                try:
                    canonicalize_evm_address(getattr(backend, field_name))
                except (AttributeError, TypeError, ValueError):
                    missing.append(f"BUDGET_BACKEND_{field_name.upper()}")
            for method in ("prepare", "broadcast", "verify", "pending_nonce", "allowance"):
                if not callable(getattr(backend, method, None)):
                    missing.append(f"BUDGET_BACKEND_{method.upper()}")
        backend_ready = not missing
        spender = config.budget_contract_address
        relayer_address = None
        if backend is not None and isinstance(getattr(backend, "relayer_address", None), str):
            try:
                relayer_address = canonicalize_evm_address(backend.relayer_address)
            except (TypeError, ValueError):
                relayer_address = None
        return {
            "service": "funding_service",
            "status": "ready" if backend_ready else "blocked",
            "ready": not missing,
            "settlement_rail": _BUDGET_RAIL,
            # This profile is the explicitly enabled native executor rail.  A
            # caller must still require ``status == ready`` before submitting.
            "live_funding_enabled": True,
            "native_facilitator_enabled": True,
            "native_facilitator_ready": backend_ready,
            "hosted_facilitator_enabled": False,
            "hosted_facilitator_ready": False,
            "relayer_address": relayer_address,
            "universal_payer_ready": False,
            "payer_address": None,
            "automatic_payment_rail": _BUDGET_RAIL if backend_ready else None,
            "supported_assets": {config.budget_network: config.budget_token_address},
            "spender_address": spender,
            "spender_addresses": {config.budget_network: spender},
            "spending_authorization_supported": True,
            "spending_mode": "budget_contract_execute",
            "network": config.budget_network,
            "token": config.budget_token_symbol,
            "token_address": config.budget_token_address,
            "chain_id": config.budget_chain_id,
            "token_symbol": config.budget_token_symbol,
            "executor_contract": config.budget_contract_address,
            "payee": config.budget_payee_address,
            "missing": missing,
            "warnings": [],
            "next_action": "authorize_spending_cap_or_spend"
            if backend_ready
            else "configure_budget_backend",
            "mandate_limits_enforced": [
                "per_transaction",
                "rolling_hour",
                "daily",
                "total",
            ],
            "test_only": True,
        }
