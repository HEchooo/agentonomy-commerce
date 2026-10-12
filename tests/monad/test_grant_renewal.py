from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from sqlalchemy import select

from apps.facilitator.budget_protocol import sign_grant
from examples.monad_commerce import public_core
from examples.monad_commerce.hosted_core import HostedWalletCore
from examples.monad_commerce.hosted_canary import HostedCanary
from examples.monad_commerce.public_canary import PublicCanary
from examples.monad_commerce.hosted_process import dispatch_hosted
from examples.monad_commerce.opc_bridge import CommerceOpcClient
from services.account_service.repository import AuditEventRow, SpendingGrantRow
from shared.hosted_facilitator_protocol import DeviceSigningKey
from shared.opc_protocol import sign_opc_proof

pytest_plugins = ["test_hosted_core"]

from test_hosted_core import (
    APPROVAL_TX,
    EXECUTOR,
    OWNER,
    _browser_login,
    _onboard,
    _sign_personal,
)


def _expire_grant(core: HostedWalletCore) -> str:
    grant_id = core.grant.spending_grant_id
    with core.repository._write_session() as session:
        row = session.get(SpendingGrantRow, grant_id)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    return grant_id


def _renew(core: HostedWalletCore, browser: str, csrf: str) -> dict:
    challenge = core.grant_renew_challenge(browser, csrf)
    result = core.grant_renew_verify(_sign_personal(challenge["message_to_sign"], OWNER))
    return {"challenge": challenge, "result": result}


@pytest.mark.parametrize("terminal", ["expired", "revoked", "exhausted"])
def test_terminal_grant_renewal_uses_new_generation_and_preserves_history(
    core_factory, tmp_path: Path, terminal: str
):
    core = core_factory(tmp_path)
    try:
        browser, csrf, _ = _onboard(core, f"renew-{terminal}")
        old_grant_id = core.grant.spending_grant_id
        old_binding_id = core.budget_binding.binding_id
        old_allowance_id = core.allowance.asset_allowance_id
        old_request = core._metadata["grant_request"]
        old_challenge = core._metadata["grant_challenge"]
        old_signature = core._metadata["grant_signature"]
        with core.repository.sessions() as session:
            old_audits = session.scalar(select(AuditEventRow).where(
                AuditEventRow.event_type == "grant_created"
            ))

        if terminal == "expired":
            _expire_grant(core)
        elif terminal == "revoked":
            core.revoke()
            operator = tmp_path / "operator"
            operator.mkdir()
            chain_grant_id = core.budget_binding.grant_id
            (operator / "wallet-operations.json").write_text(
                __import__("json").dumps({
                    "revocation": {
                        "spending_grant_id": old_grant_id,
                        "chain_grant_id": chain_grant_id,
                        "core_revoked": True,
                        "verified": True,
                        "chain_revoked": True,
                        "status": "verified",
                    }
                })
            )
        else:
            with core.repository._write_session() as session:
                row = session.get(SpendingGrantRow, old_grant_id)
                assert row is not None
                row.status = "exhausted"

        assert core._phase() in {"expired", "revoked"}
        renewed = _renew(core, browser, csrf)
        new_grant_id = renewed["result"]["spending_grant_id"]
        assert new_grant_id != old_grant_id
        assert core.grant.spending_grant_id == new_grant_id
        assert core.budget_binding is None
        assert core.allowance is None
        assert core._phase() == "budget_grant"

        history = core._metadata["grant_history"]
        assert len(history) == 1
        previous = history[0]
        assert previous["spending_grant_id"] == old_grant_id
        assert previous["budget_binding_id"] == old_binding_id
        assert previous["allowance_id"] == old_allowance_id
        assert previous["grant_request"] == old_request
        assert previous["grant_challenge"] == old_challenge
        assert previous["grant_signature"] == old_signature
        assert core.repository.spending_grant(old_grant_id).status in {"expired", "revoked", "exhausted"}
        assert core.funding_service.get_budget_binding(binding_id=old_binding_id) is not None
        assert old_audits is not None

        payload = core.budget_payload()
        assert payload["grant"]["grantId"] != previous["budget_grant"]["grantId"]
        owner_signature = "0x" + __import__("apps.facilitator.budget_protocol", fromlist=["sign_grant"]).sign_grant(
            payload["grant"], OWNER.key, 31337, EXECUTOR
        ).hex()
        binding = core.budget_bind(owner_signature)
        assert binding["binding_id"] != old_binding_id
        with pytest.raises(ValueError, match="different|invalid|scope"):
            core.budget_bind(previous["budget_signature"])

        # Renewal forces a fresh allowance observation even when the same
        # approval transaction still covers the budget.
        assert core._phase() == "allowance"
        allowance = core.allowance_verify(APPROVAL_TX)
        assert allowance["allowance_id"] == old_allowance_id
        assert core._phase() == "ready"
    finally:
        core.close()


def test_active_grant_cannot_use_renewal_operation(core_factory, tmp_path: Path):
    core = core_factory(tmp_path)
    try:
        browser, csrf, _ = _onboard(core, "renew-active")
        with pytest.raises(ValueError, match="active|terminal|unavailable"):
            core.grant_renew_challenge(browser, csrf)
        assert core.grant_challenge(browser, csrf)["status"] == "verified"
    finally:
        core.close()


def test_renewal_response_loss_replays_original_challenge_without_duplicate_grant(
    core_factory, tmp_path: Path
):
    core = core_factory(tmp_path)
    try:
        browser, csrf, _ = _onboard(core, "renew-replay")
        old_grant_id = core.grant.spending_grant_id
        _expire_grant(core)
        challenge = core.grant_renew_challenge(browser, csrf)
        signature = _sign_personal(challenge["message_to_sign"], OWNER)
        first = core.grant_renew_verify(signature)
        new_grant_id = first["spending_grant_id"]
        assert new_grant_id != old_grant_id
        # A retry after the caller lost the response is an exact replay.
        replay = core.grant_renew_verify(signature)
        assert replay["spending_grant_id"] == new_grant_id
        assert len(core._metadata["grant_history"]) == 1
        assert len(core.repository.active_spending_grants("commerce-demo-user")) == 1
    finally:
        core.close()


@pytest.mark.parametrize("failure_at", [1, 2])
def test_renewal_restore_after_each_metadata_commit_failure(
    core_factory, tmp_path: Path, monkeypatch, failure_at: int
):
    """A metadata write failure never loses the old grant or creates a duplicate.

    The first commit is the durable signature intent. The second is the
    promotion commit after Core has created the new row. Restart must either
    leave the old grant safely retryable or recover that one new row exactly.
    """
    core = core_factory(tmp_path)
    browser, csrf, _ = _onboard(core, f"renew-commit-{failure_at}")
    old_grant_id = core.grant.spending_grant_id
    # Mark the row terminal without mutating its signed expiry terms. This
    # mirrors an exhausted grant and keeps restart validation focused on the
    # renewal commit boundary rather than a tampered request document.
    with core.repository._write_session() as session:
        row = session.get(SpendingGrantRow, old_grant_id)
        assert row is not None
        row.status = "exhausted"
    challenge = core.grant_renew_challenge(browser, csrf)
    signature = _sign_personal(challenge["message_to_sign"], OWNER)
    original_persist = core._persist_state
    calls = 0

    def fail_one_commit():
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("test metadata commit failure")
        return original_persist()

    monkeypatch.setattr(core, "_persist_state", fail_one_commit)
    with pytest.raises(OSError, match="metadata commit failure"):
        core.grant_renew_verify(signature)
    core.close()

    restored = core_factory(tmp_path)
    try:
        if failure_at == 1:
            # Signature intent never reached disk. The old terminal grant is
            # still the only authority and the same challenge can be retried.
            assert restored.grant.spending_grant_id == old_grant_id
            assert restored._metadata["renewal_signature"] is None
            retried = _renew(restored, browser, csrf)
            assert retried["result"]["spending_grant_id"] != old_grant_id
            assert len(restored._metadata["grant_history"]) == 1
        else:
            # The new Core row exists and the promotion write was the only
            # missing commit. Restore promotes that exact row once.
            assert restored.grant.spending_grant_id != old_grant_id
            assert len(restored._metadata["grant_history"]) == 1
            assert restored._metadata["grant_history"][0]["spending_grant_id"] == old_grant_id
            assert len(restored.repository.active_spending_grants("commerce-demo-user")) == 1
            assert restored.repository.spending_grant(old_grant_id) is not None
    finally:
        restored.close()


def _journal_canary(tmp_path: Path, *, grant_id: str, chain_grant_id: str):
    canary = PublicCanary.__new__(PublicCanary)
    canary.operations_path = tmp_path / "wallet-operations.json"
    canary.wallet_operations = {
        "approval": {
            "spending_grant_id": "grant-old",
            "transaction_hash": "0x" + "aa" * 32,
            "status": "pending",
        },
        "revocation": {
            "spending_grant_id": "grant-old",
            "chain_grant_id": "0x" + "11" * 32,
            "transaction_hash": "0x" + "bb" * 32,
            "status": "pending",
            "core_revoked": True,
        },
    }
    canary.network = SimpleNamespace(
        token="0x" + "22" * 20,
        executor="0x" + "33" * 20,
        chain_id=31337,
    )
    canary.owner = "0x" + "44" * 20
    canary.market = None
    canary.core = SimpleNamespace(revoke=lambda: {"status": "revoked"})
    canary._onboarding_status = lambda: {
        "phase": "allowance",
        "spending_grant_id": grant_id,
        "chain_grant_id": chain_grant_id,
    }
    canary._wallet_transaction = lambda to, data: {"to": to, "data": data}
    canary.onboarding = lambda *args, **kwargs: {"phase": "ready"}
    return canary


def test_wallet_operation_journal_scopes_new_approval_and_revocation_to_new_grant(
    tmp_path: Path, monkeypatch
):
    new_grant = "grant-new"
    new_chain_grant = "0x" + "55" * 32
    canary = _journal_canary(tmp_path, grant_id=new_grant, chain_grant_id=new_chain_grant)

    approval = canary.approval_transaction()
    assert approval["transaction"]["to"] == canary.network.token
    assert canary.wallet_operations["approval"] == {"spending_grant_id": new_grant}
    assert canary.wallet_operations["approval_history"]["grant-old"]["transaction_hash"] == "0x" + "aa" * 32

    # Revocation must use the current chain grant and preserve the old pending
    # transaction instead of returning/reusing it.
    canary._onboarding_status = lambda: {
        "phase": "ready",
        "spending_grant_id": new_grant,
        "chain_grant_id": new_chain_grant,
    }
    prepared = canary.revoke_prepare()
    assert prepared["spending_grant_id"] == new_grant
    assert prepared["chain_grant_id"] == new_chain_grant
    assert prepared["transaction"]["data"].endswith(new_chain_grant[2:])
    assert canary.wallet_operations["revocation_history"]["grant-old"]["transaction_hash"] == "0x" + "bb" * 32

    observed = {}
    monkeypatch.setattr(
        "examples.monad_commerce.public_canary.verify_wallet_transaction",
        lambda _network, *, owner, tx_hash, to, data: observed.update(
            {"owner": owner, "tx_hash": tx_hash, "to": to, "data": data}
        ) or {"verified": True, "transaction_hash": tx_hash},
    )
    verified = canary.verify_revocation("0x" + "cc" * 32)
    assert verified["verified"] is True
    assert observed["data"].endswith(new_chain_grant[2:])
    assert canary.wallet_operations["revocation"]["spending_grant_id"] == new_grant


def test_unscoped_legacy_wallet_transaction_is_not_reused_after_generation_change(tmp_path: Path):
    canary = _journal_canary(tmp_path, grant_id="grant-new", chain_grant_id="0x" + "55" * 32)
    canary.wallet_operations["approval"] = {"transaction_hash": "0x" + "aa" * 32}
    with pytest.raises(ValueError, match="unscoped"):
        canary.approval_transaction()


def test_legacy_approval_is_migrated_only_after_independent_reverification(
    tmp_path: Path, monkeypatch
):
    canary = _journal_canary(tmp_path, grant_id="grant-old", chain_grant_id="0x" + "11" * 32)
    legacy_hash = "0x" + "aa" * 32
    canary.wallet_operations["approval"] = {
        "transaction_hash": legacy_hash,
        "status": "verified",
        "verified": True,
    }
    monkeypatch.setattr(
        "examples.monad_commerce.public_canary.verify_wallet_transaction",
        lambda *_args, **_kwargs: {"transaction_hash": legacy_hash, "two_rpc_verified": True},
    )

    canary._archive_grant_generation("grant-old", "grant-new")

    migrated = canary.wallet_operations["approval_history"]["grant-old"]
    assert migrated["spending_grant_id"] == "grant-old"
    assert migrated["two_rpc_verified"] is True
    assert canary.wallet_operations["approval"] == {"spending_grant_id": "grant-new"}


def test_unverified_legacy_approval_is_quarantined_without_blocking_new_generation(
    tmp_path: Path, monkeypatch
):
    canary = _journal_canary(tmp_path, grant_id="grant-old", chain_grant_id="0x" + "11" * 32)
    legacy_hash = "0x" + "aa" * 32
    canary.wallet_operations["approval"] = {
        "transaction_hash": legacy_hash,
        "status": "pending",
    }
    monkeypatch.setattr(
        "examples.monad_commerce.public_canary.verify_wallet_transaction",
        lambda *_args, **_kwargs: None,
    )

    canary._archive_grant_generation("grant-old", "grant-new")

    assert canary.wallet_operations["approval"] == {"spending_grant_id": "grant-new"}
    quarantine = canary.wallet_operations["approval_history"]["unscoped:" + legacy_hash]
    assert quarantine["transaction_hash"] == legacy_hash
    assert "grant-old" not in canary.wallet_operations["approval_history"]


def test_hosted_marketplace_renews_in_place_and_archives_wallet_generation(tmp_path: Path):
    class Market:
        def __init__(self):
            self.calls = []

        def request(self, method, arguments):
            self.calls.append((method, arguments))
            assert method == "onboarding"
            assert arguments["operation"] == "grant_renew_verify"
            return {"status": "verified", "spending_grant_id": "grant-new"}

    canary = HostedCanary.__new__(HostedCanary)
    canary.market = Market()
    canary.core = object()
    canary.operations_path = tmp_path / "wallet-operations.json"
    canary.wallet_operations = {
        "approval": {"spending_grant_id": "grant-old", "transaction_hash": "0x" + "aa" * 32},
        "revocation": {"spending_grant_id": "grant-old", "chain_grant_id": "0x" + "11" * 32},
    }
    canary._onboarding_status = lambda: {"spending_grant_id": "grant-old"}

    result = canary.onboarding("grant_renew_verify", signature="0x" + "cc" * 65)

    assert result["spending_grant_id"] == "grant-new"
    assert canary.market.calls == [
        ("onboarding", {"operation": "grant_renew_verify", "signature": "0x" + "cc" * 65})
    ]
    assert canary.wallet_operations["approval"]["spending_grant_id"] == "grant-new"
    assert "transaction_hash" not in canary.wallet_operations["approval"]
    assert canary.wallet_operations["approval_history"]["grant-old"]["transaction_hash"] == "0x" + "aa" * 32
    assert canary.wallet_operations["revocation_history"]["grant-old"]["spending_grant_id"] == "grant-old"


def test_renewal_restart_keeps_old_binding_history_and_rejects_old_opc_token(
    core_factory, tmp_path: Path
):
    core = core_factory(tmp_path)
    try:
        browser, csrf, _ = _onboard(core, "renew-restart")
        device = DeviceSigningKey.generate()

        def proof(action):
            return sign_opc_proof(
                device,
                origin="http://127.0.0.1:8091",
                allow_loopback_http=True,
                action=action,
                request_id="renew-restart-" + action,
                now=int(time.time()),
            )

        pairing = core.opc_pair(proof("pair"), browser, csrf)
        installation = core.opc_approve(
            browser,
            csrf,
            pairing["session_id"],
            _sign_personal(pairing["message_to_sign"], OWNER),
        )
        token = core.opc_token(proof("token"))["access_token"]
        old_grant_id = core.grant.spending_grant_id
        old_binding_id = core.budget_binding.binding_id
        _expire_grant(core)
        _renew(core, browser, csrf)
        new_payload = core.budget_payload()
        new_signature = "0x" + sign_grant(new_payload["grant"], OWNER.key, 31337, EXECUTOR).hex()
        core.budget_bind(new_signature)
        core.allowance_verify(APPROVAL_TX)
        with pytest.raises(ValueError, match="authorization|grant|authority|active"):
            core.opc_authenticate(token)
        assert core.opc_status(proof("status"))["status"] == "consent_required"
        core.close()

        restored = core_factory(tmp_path)
        try:
            assert restored.grant.spending_grant_id != old_grant_id
            assert restored._phase() == "ready"
            assert restored._metadata["grant_history"][0]["budget_binding_id"] == old_binding_id
            assert restored.funding_service.get_budget_binding(binding_id=old_binding_id) is not None
            with pytest.raises(ValueError, match="authorization|grant|authority|active"):
                restored.opc_authenticate(token)
            assert restored.onboarding_status()["spending_grant_id"] == restored.grant.spending_grant_id
            assert installation["spending_grant_id"] == old_grant_id
        finally:
            restored.close()
    finally:
        if core._state_lock is not None:
            core.close()


def test_legacy_onboarding_metadata_is_migrated_without_losing_authority(
    core_factory, tmp_path: Path
):
    core = core_factory(tmp_path)
    try:
        _onboard(core, "renew-legacy")
        core.close()
        metadata_path = tmp_path / "public-onboarding.json"
        metadata = __import__("json").loads(metadata_path.read_text())
        for key in (
            "allowance_requires_refresh",
            "grant_history",
            "renewal_request",
            "renewal_challenge",
            "renewal_signature",
        ):
            metadata.pop(key)
        metadata["version"] = 1
        metadata_path.write_text(__import__("json").dumps(metadata))

        restored = core_factory(tmp_path)
        try:
            migrated = __import__("json").loads(metadata_path.read_text())
            assert migrated["version"] == 2
            assert migrated["grant_history"] == []
            assert restored._phase() == "ready"
        finally:
            restored.close()
    finally:
        if core._state_lock is not None:
            core.close()


def test_corrupt_non_object_metadata_fails_closed_with_core_error(
    core_factory, tmp_path: Path
):
    core = core_factory(tmp_path)
    core.close()
    metadata_path = tmp_path / "public-onboarding.json"
    metadata_path.write_text("[]")
    with pytest.raises(public_core.CorePersistenceError, match="metadata fields"):
        core_factory(tmp_path)


def test_renewal_blocks_unresolved_funding_and_unproven_revocation(
    core_factory, tmp_path: Path
):
    core = core_factory(tmp_path)
    try:
        browser, csrf, _ = _onboard(core, "renew-safety")
        _expire_grant(core)
        class PendingTransaction:
            def list(self, record_type):
                assert record_type == "reservation"
                return [{"state": "spending_reserved"}]

        class PendingContext:
            def __enter__(self):
                return PendingTransaction()

            def __exit__(self, *_):
                return False

        core.funding_service.ledger.transaction = lambda: PendingContext()
        with pytest.raises(ValueError, match="unresolved|pending|renewal"):
            core.grant_renew_challenge(browser, csrf)
    finally:
        core.close()


@pytest.mark.parametrize(
    "change",
    [
        {"spending_grant_id": "another-grant"},
        {"chain_grant_id": "0x" + "99" * 32},
        {"status": "pending"},
    ],
)
def test_hosted_revocation_proof_is_bound_to_current_grant_generation(
    core_factory, tmp_path: Path, change: dict
):
    core = core_factory(tmp_path)
    try:
        browser, csrf, _ = _onboard(core, "renew-revocation-scope")
        old_grant_id = core.grant.spending_grant_id
        chain_grant_id = core.budget_binding.grant_id
        core.revoke()
        record = {
            "spending_grant_id": old_grant_id,
            "chain_grant_id": chain_grant_id,
            "core_revoked": True,
            "verified": True,
            "chain_revoked": True,
            "status": "verified",
        }
        record.update(change)
        operator = tmp_path / "operator"
        operator.mkdir(exist_ok=True)
        (operator / "wallet-operations.json").write_text(
            __import__("json").dumps({"revocation": record})
        )
        with pytest.raises(ValueError, match="chain revocation|renewal"):
            core.grant_renew_challenge(browser, csrf)
    finally:
        core.close()


def test_renewal_after_relogin_uses_new_browser_session_and_survives_restart(
    core_factory, tmp_path: Path
):
    core = core_factory(tmp_path)
    try:
        first_browser, _first_csrf, _ = _onboard(core, "renew-relogin")
        old_grant_id = core.grant.spending_grant_id
        _expire_grant(core)
        core.browser_logout(first_browser)
        browser, csrf, _ = _browser_login(core, "renew-relogin-new-browser")
        challenge = core.grant_renew_challenge(browser, csrf)
        account_session = core.repository.account_session(challenge["session_id"])
        assert account_session is not None
        renewal_public_session_id = account_session.created_by_public_account_session_id
        assert renewal_public_session_id != first_browser
        renewed = core.grant_renew_verify(
            _sign_personal(challenge["message_to_sign"], OWNER)
        )
        assert renewed["spending_grant_id"] != old_grant_id
        metadata_path = tmp_path / "public-onboarding.json"
        core.close()
        restored = core_factory(tmp_path)
        try:
            assert restored.grant.spending_grant_id == renewed["spending_grant_id"]
            assert restored._metadata["grant_history"][0]["spending_grant_id"] == old_grant_id
            archived_challenge = restored._metadata["grant_history"][0]["renewal_challenge"]
            archived_session = restored.repository.account_session(archived_challenge["session_id"])
            assert archived_session is not None
            assert archived_session.created_by_public_account_session_id == renewal_public_session_id
            assert metadata_path.exists()
        finally:
            restored.close()
    finally:
        if getattr(core, "_closed", False) is False:
            core.close()


def test_hosted_renewal_dispatch_requires_browser_and_signature():
    runtime = type(
        "Runtime",
        (),
        {
            "grant_renew_challenge": lambda self, **params: params,
            "grant_renew_verify": lambda self, **params: params,
        },
    )()
    params = {"browser_digest": "a" * 64, "csrf_digest": "b" * 64}
    assert dispatch_hosted(runtime, "grant_renew_challenge", params) == params
    assert dispatch_hosted(runtime, "grant_renew_verify", {"signature": "x"}) == {"signature": "x"}
    with pytest.raises(ValueError, match="operation outside hosted Core scope"):
        dispatch_hosted(runtime, "grant_renew_challenge", {})
