from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import stat
import time
from types import SimpleNamespace

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from sqlalchemy import func, select

from apps.facilitator.budget_protocol import sign_grant
from examples.monad_commerce import public_core
from examples.monad_commerce.public_core import CorePersistenceError
from examples.monad_commerce.hosted_core import HostedWalletCore
from examples.monad_commerce.hosted_process import dispatch_hosted
from services.account_service.repository import (
    AccountSessionRow,
    AuditEventRow,
    PublicAccountSessionRow,
    SpendingGrantRow,
)
from shared.opc_protocol import sign_opc_proof
from shared.hosted_facilitator_protocol import DeviceSigningKey


OWNER = Account.from_key("0x" + "11" * 32)
EXECUTION_SIGNER = Account.from_key("0x" + "22" * 32)
RELAYER = Account.from_key("0x" + "33" * 32)
TOKEN = "0x" + "44" * 20
EXECUTOR = "0x" + "55" * 20
PAYEE = "0x" + "66" * 20
APPROVAL_TX = "0x" + "aa" * 32
OPC_ORIGIN = "http://127.0.0.1:8091"


def test_external_device_authorization_resolution_uses_live_core_consent(core_factory, tmp_path):
    core = core_factory(tmp_path)
    try:
        browser, csrf, _ = _onboard(core)
        device = DeviceSigningKey.generate()
        def proof(action):
            return sign_opc_proof(device, origin=OPC_ORIGIN, allow_loopback_http=True,
                                  action=action, request_id='external-' + action, now=int(time.time()))
        pairing = core.opc_pair(proof('pair'), browser, csrf)
        core.opc_approve(browser, csrf, pairing['session_id'], _sign_personal(pairing['message_to_sign'], OWNER))
        request = {'user_id': 'commerce-demo-user', 'agent_id': 'hermes',
                   'opc_installation_id': pairing['installation_id'], 'product': 'marketplace',
                   'venue': 'clink_marketplace', 'merchant': 'commerce_analytics',
                   'merchant_trust_tier': 'clink_verified', 'network': 'eip155:31337',
                   'token_address': TOKEN, 'asset': TOKEN, 'spender_address': EXECUTOR,
                   'amount_usdc': '0.30', 'destination': PAYEE,
                   'resource': 'https://merchant.agentonomy.invalid/v1/reconcile'}
        assert core.resolve_authorization(request)['ready'] is True
        dispatch_hosted(core, 'opc_revoke', {'proof': proof('revoke')})
        assert core.resolve_authorization(request)['ready'] is False
    finally:
        core.close()


@pytest.mark.parametrize('expiry_target', ['device', 'grant'])
def test_external_device_status_matches_bridge_and_effective_expiry(core_factory, tmp_path, expiry_target):
    from services.account_service.opc_service import OpcInstallationRow
    from examples.monad_commerce.external_opc import public_device_status
    from examples.monad_commerce.opc_bridge import CommerceOpcClient
    core = core_factory(tmp_path)
    try:
        browser, csrf, _ = _onboard(core)
        device = DeviceSigningKey.generate()
        def proof(action):
            return sign_opc_proof(device, origin=OPC_ORIGIN, allow_loopback_http=True,
                                  action=action, request_id='contract-' + action, now=int(time.time()))
        pairing = core.opc_pair(proof('pair'), browser, csrf)
        core.opc_approve(browser, csrf, pairing['session_id'], _sign_personal(pairing['message_to_sign'], OWNER))
        raw = core.opc_status(proof('status'))
        assert raw['status'] == 'active' and 'user_id' in raw
        client = CommerceOpcClient.__new__(CommerceOpcClient)
        client.state = SimpleNamespace(installation_id=pairing['installation_id'])
        assert client._valid_status_response(public_device_status(json.loads(json.dumps(raw, default=str))))
        assert 'spending_grant_id' not in public_device_status(raw)
        with core.repository._write_session() as tx:
            if expiry_target == 'device':
                tx.get(OpcInstallationRow, pairing['installation_id']).consent_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            else:
                tx.get(SpendingGrantRow, core.grant.spending_grant_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        expired = core.opc_status(proof('status'))
        assert expired['status'] == 'consent_required'
        assert client._valid_status_response(public_device_status(json.loads(json.dumps(expired, default=str))))
        with pytest.raises(ValueError):
            core.opc_token(proof('token'))
    finally:
        core.close()


class _Signer:
    def sign_execution(self, grant, owner_signature, execution):
        del grant, owner_signature, execution
        return b"\x00" * 65

    def sign_transaction(self, transaction):
        del transaction
        return b""


class _FakeRpc:
    current_allowance = 1_000_000

    def __init__(self, url: str, *, writable: bool = False):
        self.url = url
        self.writable = writable

    def check_chain(self, chain_id: int) -> None:
        assert chain_id == 31337

    def call(self, method: str, params: list):
        if method == "eth_chainId":
            return hex(31337)
        if method == "eth_blockNumber":
            return "0x5"
        if method == "eth_call":
            return hex(self.current_allowance)
        if method == "eth_getTransactionReceipt":
            return {
                "status": "0x1",
                "transactionHash": APPROVAL_TX,
                "blockNumber": "0x5",
                "blockHash": "0x" + "bb" * 32,
            }
        if method == "eth_getTransactionByHash":
            spender = EXECUTOR[2:].rjust(64, "0")
            return {
                "hash": APPROVAL_TX,
                "from": OWNER.address,
                "to": TOKEN,
                "input": "0x095ea7b3" + spender + (1_000_000).to_bytes(32, "big").hex(),
                "blockNumber": "0x5",
                "blockHash": "0x" + "bb" * 32,
            }
        if method == "eth_getTransactionCount":
            return "0x0"
        raise AssertionError(method)


class _FakeBudgetBackend:
    def __init__(self, config, *, relayer_address, execution_signer_address, **kwargs):
        del config, kwargs
        self.relayer_address = relayer_address.lower()
        self.execution_signer_address = execution_signer_address.lower()

    def prepare(self, row, binding, nonce):
        raise AssertionError("settlement is outside HostedWalletCore tests")

    def broadcast(self, attempt):
        raise AssertionError("settlement is outside HostedWalletCore tests")

    def verify(self, row, binding, attempt):
        raise AssertionError("settlement is outside HostedWalletCore tests")

    def pending_nonce(self):
        return 0

    def allowance(self, owner):
        del owner
        return _FakeRpc.current_allowance


def _bootstrap(**changes):
    values = {
        "mode": "local_anvil",
        "chain_id": 31337,
        "rpc_urls": ["http://127.0.0.1:18545", "http://127.0.0.1:18546"],
        "token": TOKEN,
        "executor": EXECUTOR,
        "payee": PAYEE,
        "gas_limit": 123_456,
        "max_gas_price_wei": 2_000_000_000,
        "owner": OWNER.address,
        "execution_signer": EXECUTION_SIGNER.address,
        "relayer": RELAYER.address,
    }
    return values | changes


@pytest.fixture
def core_factory(monkeypatch):
    monkeypatch.setattr(public_core, "RpcClient", _FakeRpc)
    monkeypatch.setattr(public_core, "BudgetBackend", _FakeBudgetBackend)

    def create(tmp_path: Path, **changes):
        _FakeRpc.current_allowance = 1_000_000
        return HostedWalletCore(
            tmp_path,
            _bootstrap(**changes),
            _Signer(),
            allow_local=True,
            opc_origin=OPC_ORIGIN,
            allow_loopback_http=True,
        )

    return create


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _sign_personal(message: str, account: Account) -> str:
    return Account.sign_message(
        encode_defunct(text=message), account.key
    ).signature.hex()


def _browser_login(core: HostedWalletCore, label: str) -> tuple[str, str, dict]:
    browser_digest = _digest(f"browser-{label}")
    csrf_digest = _digest(f"csrf-{label}")
    challenge = core.browser_challenge(browser_digest, csrf_digest)
    result = core.browser_verify(
        browser_digest,
        csrf_digest,
        challenge["session_id"],
        _sign_personal(challenge["message_to_sign"], OWNER),
    )
    return browser_digest, csrf_digest, result


def _onboard(core: HostedWalletCore, label: str = "first") -> tuple[str, str, dict]:
    browser_digest, csrf_digest, wallet = _browser_login(core, label)
    grant_challenge = core.grant_challenge(browser_digest, csrf_digest)
    grant = core.grant_verify(_sign_personal(grant_challenge["message_to_sign"], OWNER))
    payload = core.budget_payload()
    owner_signature = (
        "0x" + sign_grant(payload["grant"], OWNER.key, 31337, EXECUTOR).hex()
    )
    binding = core.budget_bind(owner_signature)
    allowance = core.allowance_verify(APPROVAL_TX)
    assert core.budget_binding.spending_grant_id == grant["spending_grant_id"]
    assert binding["binding_id"] == core.budget_binding.binding_id
    assert allowance["allowance_id"]
    return browser_digest, csrf_digest, wallet


def test_browser_challenge_is_digest_bound_and_has_distinct_session_and_challenge_ttls(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    try:
        browser_digest = _digest("browser-one")
        csrf_digest = _digest("csrf-one")
        challenge = core.browser_challenge(browser_digest, csrf_digest)

        assert challenge["signing_method"] == "personal_sign"
        assert challenge["message_to_sign"]
        assert challenge["session_id"]
        assert challenge["public_account_session_id"]
        assert challenge["expires_at"]
        assert challenge["browser_expires_at"]
        assert not any(
            key in challenge
            for key in ("token_digest", "browser_session_digest", "csrf_token_digest")
        )
        session = core.repository.public_account_session_by_id(
            challenge["public_account_session_id"]
        )
        account_session = core.repository.account_session(challenge["session_id"])
        assert session is not None and account_session is not None
        assert session.browser_session_digest == browser_digest
        assert session.csrf_token_digest == csrf_digest
        assert session.expires_at > account_session.expires_at
        assert session.expires_at - datetime.now(UTC) > public_core.CHALLENGE_TTL
        assert session.expires_at - datetime.now(UTC) < core.BROWSER_SESSION_TTL

        other_browser = _digest("browser-two")
        other_csrf = _digest("csrf-two")
        core.browser_challenge(other_browser, other_csrf)
        signature = _sign_personal(challenge["message_to_sign"], OWNER)
        with pytest.raises(ValueError, match="bound|session"):
            core.browser_verify(
                other_browser, other_csrf, challenge["session_id"], signature
            )
        with pytest.raises(ValueError, match="signature|wallet"):
            core.browser_verify(
                browser_digest,
                csrf_digest,
                challenge["session_id"],
                _sign_personal(challenge["message_to_sign"], EXECUTION_SIGNER),
            )
        core.browser_verify(
            browser_digest, csrf_digest, challenge["session_id"], signature
        )
        with pytest.raises(ValueError, match="consumed"):
            core.browser_verify(
                browser_digest, csrf_digest, challenge["session_id"], signature
            )
        with pytest.raises(ValueError, match="digest"):
            core.browser_challenge("not-a-digest", csrf_digest)
    finally:
        core.close()


def test_relogin_reuses_identity_grant_and_budget_and_logout_only_revokes_browser_session(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    try:
        first_browser, first_csrf, first_wallet = _onboard(core)
        identity_id = first_wallet["wallet_identity_id"]
        grant_id = core.grant.spending_grant_id
        binding_id = core.budget_binding.binding_id
        allowance_id = core.allowance.asset_allowance_id

        second_browser, second_csrf, second_wallet = _browser_login(core, "second")
        assert second_wallet["wallet_identity_id"] == identity_id
        assert len(core.repository.active_wallet_identities("commerce-demo-user")) == 1
        assert core.grant.spending_grant_id == grant_id
        assert core.budget_binding.binding_id == binding_id
        assert core.allowance.asset_allowance_id == allowance_id
        before_counters = core.snapshot()
        assert (
            core.browser_authorize(second_browser, second_csrf)["wallet_identity_id"]
            == identity_id
        )
        after_counters = core.snapshot()
        assert after_counters["used_amount_usdc"] == before_counters["used_amount_usdc"]
        assert (
            after_counters["reserved_amount_usdc"]
            == before_counters["reserved_amount_usdc"]
        )
        with pytest.raises(ValueError, match="CSRF"):
            core.browser_authorize(second_browser, _digest("wrong-csrf"))

        core.browser_logout(second_browser)
        with pytest.raises(ValueError, match="session|available"):
            core.browser_authorize(second_browser, second_csrf)
        assert (
            core.browser_authorize(first_browser, first_csrf)["wallet_identity_id"]
            == identity_id
        )
        assert core.grant.spending_grant_id == grant_id
    finally:
        core.close()


def test_grant_challenge_binds_to_current_authenticated_browser_after_relogin(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    try:
        first_browser, first_csrf, _wallet = _browser_login(core, "grant-stale")
        first_session = core.repository.access_public_account_browser_session(
            first_browser, public_core._now()
        )
        assert first_session is not None
        stale_challenge = core.grant_challenge(first_browser, first_csrf)
        stale_request = dict(core._metadata["grant_request"])
        core.browser_logout(first_browser)

        second_browser, second_csrf, _wallet = _browser_login(core, "grant-current")
        second_session = core.repository.access_public_account_browser_session(
            second_browser, public_core._now()
        )
        assert second_session is not None

        challenge = core.grant_challenge(second_browser, second_csrf)
        request = core._grant_request()
        account_session = core.repository.account_session(challenge["session_id"])
        assert account_session is not None
        assert challenge["session_id"] != stale_challenge["session_id"]
        assert core._metadata["grant_request"] == stale_request
        assert request.created_by_public_account_session_id == (
            second_session.public_account_session_id
        )
        assert account_session.created_by_public_account_session_id == (
            second_session.public_account_session_id
        )
        assert request.created_by_public_account_session_id != (
            first_session.public_account_session_id
        )
        assert core.grant is None
        assert core.budget_binding is None
        assert core.allowance is None
    finally:
        core.close()


def _grant_challenge_guard_snapshot(core: HostedWalletCore, grant_id: str) -> dict:
    grant = core.repository.spending_grant(grant_id)
    assert grant is not None
    with core.repository.sessions() as session:
        account_session_count = session.scalar(
            select(func.count()).select_from(AccountSessionRow)
        )
        public_session_count = session.scalar(
            select(func.count()).select_from(PublicAccountSessionRow)
        )
        audit_count = session.scalar(
            select(func.count()).select_from(AuditEventRow)
        )
    return {
        "grant": grant.model_dump(mode="json"),
        "account_session_count": account_session_count,
        "public_session_count": public_session_count,
        "audit_count": audit_count,
        "metadata": deepcopy(core._metadata),
    }


@pytest.mark.parametrize("condition", ["expired", "revoked"])
def test_relogin_grant_challenge_fails_closed_without_recreating_grant(
    core_factory, tmp_path, condition
):
    core = core_factory(tmp_path)
    try:
        first_browser, first_csrf, _wallet = _onboard(core, f"grant-{condition}")
        grant_id = core.grant.spending_grant_id
        if condition == "expired":
            with core.repository._write_session() as session:
                row = session.get(SpendingGrantRow, grant_id)
                assert row is not None
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            core.revoke()

        core.browser_logout(first_browser)
        browser, csrf, _wallet = _browser_login(core, f"relogin-{condition}")
        assert core._phase() == condition
        before = _grant_challenge_guard_snapshot(core, grant_id)

        with pytest.raises(ValueError, match="grant|authority|expired|revoked"):
            core.grant_challenge(browser, csrf)

        after = _grant_challenge_guard_snapshot(core, grant_id)
        assert after == before
        assert core.grant.spending_grant_id == grant_id
    finally:
        core.close()


def test_hosted_grant_route_requires_current_browser_digest_and_csrf():
    runtime = SimpleNamespace(
        grant_challenge=lambda **params: params,
    )
    params = {"browser_digest": "a" * 64, "csrf_digest": "b" * 64}
    assert dispatch_hosted(runtime, "grant_challenge", params) == params
    with pytest.raises(ValueError, match="operation outside hosted Core scope"):
        dispatch_hosted(runtime, "grant_challenge", {})


def test_opc_pair_approve_token_and_restart_share_core_authority(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    browser_digest, csrf_digest, _wallet = _onboard(core)
    try:
        device_key = DeviceSigningKey.generate()
        pair_proof = sign_opc_proof(
            device_key,
            origin=OPC_ORIGIN,
            allow_loopback_http=True,
            action="pair",
            request_id="hosted-pair",
            now=int(time.time()),
        )
        pairing = core.opc_pair(pair_proof, browser_digest, csrf_digest)
        assert pairing["pairing_id"]
        assert pairing["installation_id"]
        assert pairing["session_id"]
        assert pairing["message_to_sign"]
        assert pairing["public_account_session_id"]
        assert "verification_uri" not in pairing
        assert "access_token" not in pairing

        signature = _sign_personal(pairing["message_to_sign"], OWNER)
        installation = core.opc_approve(
            browser_digest, csrf_digest, pairing["session_id"], signature
        )
        assert installation["status"] == "active"
        assert installation["spending_grant_id"] == core.grant.spending_grant_id
        assert "access_token" not in installation

        status_proof = sign_opc_proof(
            device_key,
            origin=OPC_ORIGIN,
            allow_loopback_http=True,
            action="status",
            request_id="hosted-status",
            now=int(time.time()),
        )
        assert core.opc_status(status_proof)["status"] == "active"

        token_proof = sign_opc_proof(
            device_key,
            origin=OPC_ORIGIN,
            allow_loopback_http=True,
            action="token",
            request_id="hosted-token",
            now=int(time.time()),
        )
        token = core.opc_token(token_proof)
        principal = core.opc_authenticate(token["access_token"])
        assert principal["user_id"] == "commerce-demo-user"
        assert principal["agent_id"] == "hermes"
        assert (
            principal["wallet_identity_id"] == core.wallet_identity.wallet_identity_id
        )
        assert principal["spending_grant_id"] == core.grant.spending_grant_id

        secret_path = tmp_path / "opc-token.secret"
        receipt_path = tmp_path / "receipt.secret"
        assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
        assert secret_path.read_bytes() != receipt_path.read_bytes()
    finally:
        core.close()

    restored = core_factory(tmp_path)
    try:
        assert restored.opc_authenticate(token["access_token"]) == principal
        restored.revoke()
        with pytest.raises(
            ValueError, match="consent|grant|active|authority|unavailable"
        ):
            restored.opc_authenticate(token["access_token"])
        restored.opc_service.revoke(
            sign_opc_proof(
                device_key,
                origin=OPC_ORIGIN,
                allow_loopback_http=True,
                action="revoke",
                request_id="hosted-revoke",
                now=int(time.time()),
            )
        )
        assert restored.opc_status(status_proof)["status"] == "revoked"
    finally:
        restored.close()


def test_opc_challenge_is_bound_to_the_authenticated_browser_session(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    try:
        browser_digest, csrf_digest, _wallet = _onboard(core, "one")
        other_browser, other_csrf, _other_wallet = _browser_login(core, "two")
        device_key = DeviceSigningKey.generate()
        pairing = core.opc_pair(
            sign_opc_proof(
                device_key,
                origin=OPC_ORIGIN,
                allow_loopback_http=True,
                action="pair",
                request_id="bound-pair",
                now=int(time.time()),
            ),
            browser_digest,
            csrf_digest,
        )
        signature = _sign_personal(pairing["message_to_sign"], OWNER)
        with pytest.raises(ValueError, match="bound|session|pairing"):
            core.opc_approve(
                other_browser, other_csrf, pairing["session_id"], signature
            )
    finally:
        core.close()


@pytest.mark.parametrize("orphan_kind", ["regular", "dangling"])
def test_orphan_opc_secret_is_rejected_before_core_state_creation(
    core_factory, tmp_path, orphan_kind
):
    secret_path = tmp_path / "opc-token.secret"
    if orphan_kind == "regular":
        secret_path.write_bytes(b"x" * 32)
        secret_path.chmod(0o600)
    else:
        secret_path.symlink_to(tmp_path / "missing-opc-token.secret")

    with pytest.raises(CorePersistenceError, match="incomplete"):
        core_factory(tmp_path)

    assert (
        secret_path.is_symlink()
        if orphan_kind == "dangling"
        else secret_path.exists()
    )
    assert not (tmp_path / "public-onboarding.json").exists()
    assert not (tmp_path / "core.sqlite3").exists()
    assert not (tmp_path / "receipt.secret").exists()


def test_existing_core_state_without_opc_secret_is_rejected_before_locking(
    core_factory, tmp_path
):
    for name in ("public-onboarding.json", "core.sqlite3", "receipt.secret"):
        (tmp_path / name).write_bytes(b"placeholder")

    with pytest.raises(CorePersistenceError, match="incomplete"):
        core_factory(tmp_path)

    assert not (tmp_path / "opc-token.secret").exists()


def test_opc_token_secret_rejects_symlink_even_when_state_is_complete(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    core.close()

    target = tmp_path / "external-opc-token.secret"
    target.write_bytes(b"y" * 32)
    target.chmod(0o600)
    secret_path = tmp_path / "opc-token.secret"
    secret_path.unlink()
    secret_path.symlink_to(target)

    with pytest.raises(CorePersistenceError, match="symlink"):
        core_factory(tmp_path)


def test_opc_token_secret_requires_exactly_32_bytes(core_factory, tmp_path):
    core = core_factory(tmp_path)
    core.close()

    secret_path = tmp_path / "opc-token.secret"
    secret_path.write_bytes(b"z" * 31)
    secret_path.chmod(0o600)

    with pytest.raises(CorePersistenceError, match="exactly 32 bytes"):
        core_factory(tmp_path)


def test_expired_browser_session_and_wallet_challenge_fail_closed(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    try:
        browser_digest = _digest("expired-browser")
        csrf_digest = _digest("expired-csrf")
        challenge = core.browser_challenge(browser_digest, csrf_digest)
        with core.repository._write_session() as session:
            row = session.get(
                PublicAccountSessionRow, challenge["public_account_session_id"]
            )
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)

        with pytest.raises(ValueError, match="session|available"):
            core.browser_verify(
                browser_digest,
                csrf_digest,
                challenge["session_id"],
                _sign_personal(challenge["message_to_sign"], OWNER),
            )

        second_browser = _digest("expired-challenge-browser")
        second_csrf = _digest("expired-challenge-csrf")
        second = core.browser_challenge(second_browser, second_csrf)
        with core.repository._write_session() as session:
            row = session.get(AccountSessionRow, second["session_id"])
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)

        with pytest.raises(ValueError, match="expired"):
            core.browser_verify(
                second_browser,
                second_csrf,
                second["session_id"],
                _sign_personal(second["message_to_sign"], OWNER),
            )
    finally:
        core.close()


def test_opc_token_requires_current_hosted_binding_before_issue(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    try:
        _onboard(core, "missing-binding")
        device_key = DeviceSigningKey.generate()
        token_proof = sign_opc_proof(
            device_key,
            origin=OPC_ORIGIN,
            allow_loopback_http=True,
            action="token",
            request_id="missing-binding-token",
            now=int(time.time()),
        )
        core.budget_binding = None
        with pytest.raises(ValueError, match="ready|authorization"):
            core.opc_token(token_proof)
    finally:
        core.close()
