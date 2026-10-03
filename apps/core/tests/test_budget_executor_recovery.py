"""Crash, pending and revocation recovery for the Core budget rail."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.funding_service.schemas import (
    ReleaseSpendingReservationRequest,
    SettleSpendingReservationRequest,
)
from services.funding_service.service import ReservationProvenance

try:
    from test_onchain_spend_grant import (
        NOW,
        _bind,
        _request,
        _service,
        _verified_evidence,
    )
except ImportError:  # pragma: no cover - supports direct module execution.
    from apps.core.tests.test_onchain_spend_grant import (
        NOW,
        _bind,
        _request,
        _service,
        _verified_evidence,
    )


def _reserve_and_bind(tmp_path):
    repository, service, backend = _service(tmp_path)
    provenance = ReservationProvenance(
        action=SimpleNamespace(user_id="user_1", agent_id="hermes"),
        authorization_path="unified_grant",
        product="marketplace",
        unified_references={
            "product": "marketplace",
            "wallet_identity_id": "wallet_1",
            "spending_grant_id": "grant_1",
            "asset_allowance_id": "allowance_1",
        },
    )
    with patch.object(service, "_verify_marketplace_provenance", return_value=provenance):
        reservation = service.reserve_spending(_request())
    binding = _bind(service)
    request = SettleSpendingReservationRequest(
        payment_authorization={
            "rail": "budget_contract",
            "budget_binding_id": binding.binding_id,
        }
    )
    return repository, service, backend, reservation, binding, request


def test_pending_keeps_core_reservation_and_never_rebroadcasts(tmp_path):
    _repository, service, backend, reservation, _binding, request = _reserve_and_bind(
        tmp_path
    )
    observed_states = []
    backend.broadcast_observer = lambda _attempt: observed_states.append(
        service.get_reservation(reservation["reservation_id"])["risk_prebroadcast_state"]
    )
    with patch.object(
        service,
        "_require_reservation_policy_controls",
        return_value=NOW.replace(tzinfo=None),
    ):
        pending = service.settle_reservation(reservation["reservation_id"], request)
    assert pending["state"] == "payment_submitted"
    assert pending["budget_accounting_state"] == "reserved"
    assert pending["reconciliation_status"] == "pending"
    assert len(backend.broadcast_calls) == 1
    assert len(backend.verify_calls) == 1
    assert observed_states == ["broadcasting"]

    # A retry may omit the binding hint; the durable binding still owns the
    # idempotency identity and no second broadcast is possible.
    retry = service.settle_reservation(
        reservation["reservation_id"],
        SettleSpendingReservationRequest(payment_authorization={}),
    )
    assert retry["state"] == "payment_submitted"
    assert len(backend.broadcast_calls) == 1
    assert len(backend.verify_calls) == 2

    reconciled = service.reconcile_reservation(reservation["reservation_id"])
    assert reconciled["state"] == "payment_submitted"
    assert reconciled["budget_accounting_state"] == "reserved"
    assert len(backend.broadcast_calls) == 1
    assert len(backend.verify_calls) == 3


def test_prepared_journal_crash_resumes_same_attempt_after_fresh_checks(tmp_path):
    _repository, service, backend, reservation, _binding, request = _reserve_and_bind(
        tmp_path
    )
    with patch.object(
        service,
        "_broadcast_budget_attempt",
        side_effect=RuntimeError("simulated crash after durable prepare"),
    ):
        with patch.object(
            service,
            "_require_reservation_policy_controls",
            return_value=NOW.replace(tzinfo=None),
        ):
            with pytest.raises(RuntimeError, match="simulated crash"):
                service.settle_reservation(reservation["reservation_id"], request)

    journaled = service.get_reservation(reservation["reservation_id"])
    assert journaled["state"] == "payment_submitted"
    assert journaled["risk_prebroadcast_state"] == "pending"
    assert len(backend.prepare_calls) == 1
    assert len(backend.broadcast_calls) == 0

    with patch.object(
        service,
        "_require_reservation_policy_controls",
        return_value=NOW.replace(tzinfo=None),
    ):
        recovered = service.settle_reservation(reservation["reservation_id"], request)
    assert recovered["state"] == "payment_submitted"
    assert len(backend.prepare_calls) == 1
    assert len(backend.broadcast_calls) == 1
    assert len(backend.verify_calls) == 1


@pytest.mark.parametrize("invalidator", ["revoke", "expire"])
def test_prepared_attempt_is_blocked_after_revoke_or_expiry(tmp_path, invalidator):
    repository, service, backend, reservation, _binding, request = _reserve_and_bind(
        tmp_path
    )
    with patch.object(
        service,
        "_broadcast_budget_attempt",
        side_effect=RuntimeError("simulated crash after durable prepare"),
    ):
        with patch.object(
            service,
            "_require_reservation_policy_controls",
            return_value=NOW.replace(tzinfo=None),
        ):
            with pytest.raises(RuntimeError, match="simulated crash"):
                service.settle_reservation(reservation["reservation_id"], request)

    if invalidator == "revoke":
        repository.revoke_spending_grant("grant_1", NOW)
    else:
        service._utc_now = lambda: NOW.replace(day=11, tzinfo=None)

    with pytest.raises(ValueError, match="active spending grant"):
        service.settle_reservation(reservation["reservation_id"], request)
    blocked = service.get_reservation(reservation["reservation_id"])
    assert blocked["state"] == "payment_submitted"
    assert blocked["risk_prebroadcast_state"] == "pending"
    assert len(backend.prepare_calls) == 1
    assert backend.broadcast_calls == []


def test_broadcast_claim_ambiguous_recovery_is_verify_only(tmp_path):
    _repository, service, backend, reservation, _binding, request = _reserve_and_bind(
        tmp_path
    )
    backend.broadcast_error = RuntimeError("transport outcome unknown")
    with patch.object(
        service,
        "_require_reservation_policy_controls",
        return_value=NOW.replace(tzinfo=None),
    ):
        ambiguous = service.settle_reservation(reservation["reservation_id"], request)
    assert ambiguous["state"] == "payment_submitted"
    assert ambiguous["risk_prebroadcast_state"] == "ambiguous"
    assert len(backend.broadcast_calls) == 1

    backend.broadcast_error = None
    retry = service.settle_reservation(reservation["reservation_id"], request)
    assert retry["state"] == "payment_submitted"
    assert retry["risk_prebroadcast_state"] == "ambiguous"
    assert len(backend.broadcast_calls) == 1
    assert len(backend.verify_calls) == 2


def test_verified_reverted_budget_payment_releases_for_manual_review(tmp_path):
    _repository, service, backend, reservation, binding, request = _reserve_and_bind(
        tmp_path
    )
    backend._evidence = _verified_evidence(binding, status="reverted")
    with patch.object(
        service,
        "_require_reservation_policy_controls",
        return_value=NOW.replace(tzinfo=None),
    ):
        reverted = service.settle_reservation(reservation["reservation_id"], request)
    assert reverted["state"] == "unpaid_terminal"
    assert reverted["budget_accounting_state"] == "released"
    assert reverted["reconciliation_status"] == "manual_review_required"
    assert reverted["next_action"] == "operator_reconcile"
    assert len(backend.broadcast_calls) == 1

    replay = service.reconcile_reservation(reservation["reservation_id"])
    assert replay["state"] == "unpaid_terminal"
    assert len(backend.broadcast_calls) == 1
    assert len(backend.verify_calls) == 1


def test_loose_revert_status_stays_pending_and_cannot_release(tmp_path):
    _repository, service, backend, reservation, _binding, request = _reserve_and_bind(
        tmp_path
    )
    backend._evidence = {"verified": True, "status": "reverted"}
    with patch.object(
        service,
        "_require_reservation_policy_controls",
        return_value=NOW.replace(tzinfo=None),
    ):
        pending = service.settle_reservation(reservation["reservation_id"], request)
    assert pending["state"] == "payment_submitted"
    assert pending["budget_accounting_state"] == "reserved"
    with pytest.raises(ValueError, match="submitted or settled"):
        service.release_reservation(
            reservation["reservation_id"],
            ReleaseSpendingReservationRequest(reason="untrusted revert status"),
        )


def test_valid_revert_release_is_proof_gated_and_idempotent(tmp_path):
    _repository, service, backend, reservation, binding, request = _reserve_and_bind(
        tmp_path
    )
    backend._evidence = _verified_evidence(binding, status="reverted")
    with patch.object(
        service,
        "_require_reservation_policy_controls",
        return_value=NOW.replace(tzinfo=None),
    ):
        terminal = service.settle_reservation(reservation["reservation_id"], request)
    released = service.release_reservation(
        reservation["reservation_id"],
        ReleaseSpendingReservationRequest(reason="verified final revert"),
    )
    assert terminal["state"] == "unpaid_terminal"
    assert terminal["budget_accounting_state"] == "released"
    assert released["state"] == "unpaid_terminal"
    assert released["budget_accounting_state"] == "released"
    replay = service.release_reservation(
        reservation["reservation_id"],
        ReleaseSpendingReservationRequest(reason="duplicate release"),
    )
    assert replay["state"] == "unpaid_terminal"
    assert replay["budget_accounting_state"] == "released"


def test_monad_mode_rejects_local_finality_evidence(tmp_path):
    _repository, service, backend, reservation, binding, request = _reserve_and_bind(
        tmp_path
    )
    service.budget_config.budget_mode = "monad_testnet"
    backend._evidence = _verified_evidence(binding)
    with patch.object(
        service,
        "_require_reservation_policy_controls",
        return_value=NOW.replace(tzinfo=None),
    ):
        pending = service.settle_reservation(reservation["reservation_id"], request)
    assert pending["state"] == "payment_submitted"
    assert pending["budget_accounting_state"] == "reserved"


def test_unfinalized_evidence_cannot_complete_core_budget(tmp_path):
    _repository, service, backend, reservation, binding, request = _reserve_and_bind(
        tmp_path
    )
    # ``status=finalized`` without the explicitly required ``state=finalized``
    # is rejected, even though all other fields look complete.
    backend._evidence = _verified_evidence(binding, status="finalized")
    with patch.object(
        service,
        "_require_reservation_policy_controls",
        return_value=NOW.replace(tzinfo=None),
    ):
        pending = service.settle_reservation(reservation["reservation_id"], request)
    assert pending["state"] == "payment_submitted"
    assert pending["budget_accounting_state"] == "reserved"
    assert pending["reconciliation_status"] == "pending"
    assert len(backend.broadcast_calls) == 1


def test_core_grant_revoke_after_broadcast_does_not_block_chain_reconciliation(tmp_path):
    repository, service, backend, reservation, binding, request = _reserve_and_bind(
        tmp_path
    )
    with patch.object(
        service,
        "_require_reservation_policy_controls",
        return_value=NOW.replace(tzinfo=None),
    ):
        pending = service.settle_reservation(reservation["reservation_id"], request)
    assert pending["state"] == "payment_submitted"
    assert len(backend.broadcast_calls) == 1

    repository.revoke_spending_grant("grant_1", NOW)
    backend._evidence = _verified_evidence(binding)
    settled = service.reconcile_reservation(reservation["reservation_id"])

    assert settled["state"] == "settled"
    assert settled["budget_accounting_state"] == "settled"
    assert len(backend.broadcast_calls) == 1
    assert len(backend.verify_calls) == 2
