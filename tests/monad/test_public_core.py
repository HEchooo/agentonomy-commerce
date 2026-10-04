from __future__ import annotations

import json
from pathlib import Path
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from apps.facilitator.budget_protocol import hash_grant, sign_grant
from examples.monad_commerce import public_core
from examples.monad_commerce.public_core import CorePersistenceError, ExternalWalletCore
from services.funding_service.budget_binding import derive_agent_scope


OWNER = Account.from_key("0x" + "11" * 32)
EXECUTION_SIGNER = Account.from_key("0x" + "22" * 32)
RELAYER = Account.from_key("0x" + "33" * 32)
TOKEN = "0x" + "44" * 20
EXECUTOR = "0x" + "55" * 20
PAYEE = "0x" + "66" * 20
APPROVAL_TX = "0x" + "aa" * 32


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
        raise AssertionError("settlement is outside onboarding tests")

    def broadcast(self, attempt):
        raise AssertionError("settlement is outside onboarding tests")

    def verify(self, row, binding, attempt):
        raise AssertionError("settlement is outside onboarding tests")

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
        return ExternalWalletCore(
            tmp_path,
            _bootstrap(**changes),
            _Signer(),
            allow_local=True,
        )

    return create


def _sign_personal(message: str, account: Account) -> str:
    return Account.sign_message(encode_defunct(text=message), account.key).signature.hex()


def test_bootstrap_rejects_secret_or_unknown_fields_and_local_requires_python_opt_in(tmp_path):
    with pytest.raises(ValueError, match="bootstrap"):
        ExternalWalletCore(tmp_path / "secret", _bootstrap(owner_key="0x" + "11" * 32), _Signer())
    with pytest.raises(ValueError, match="bootstrap"):
        ExternalWalletCore(tmp_path / "unknown", _bootstrap(unexpected="value"), _Signer())
    with pytest.raises(ValueError, match="local"):
        ExternalWalletCore(tmp_path / "local", _bootstrap(), _Signer())


def test_staged_external_signatures_bind_fixed_scope_and_allowance(core_factory, tmp_path):
    core = core_factory(tmp_path)
    try:
        assert core.onboarding_status()["phase"] == "wallet"
        challenge = core.wallet_challenge()
        public_session = core.repository.public_account_session_by_id(
            challenge["public_account_session_id"]
        )
        assert public_session is not None
        assert public_session.exchanged_at is not None
        assert public_session.browser_session_digest
        assert public_session.csrf_token_digest
        with pytest.raises(ValueError):
            core.wallet_verify(_sign_personal(challenge["message_to_sign"], EXECUTION_SIGNER))

        wallet = core.wallet_verify(_sign_personal(challenge["message_to_sign"], OWNER))
        assert wallet["wallet_address"] == OWNER.address.lower()
        grant_challenge = core.grant_challenge()
        grant = core.grant_verify(_sign_personal(grant_challenge["message_to_sign"], OWNER))
        assert grant["spending_grant_id"]

        payload = core.budget_payload()
        assert payload["grant"]["agentScope"] == derive_agent_scope(
            "hermes", grant["spending_grant_id"]
        )
        assert payload["digest"] == "0x" + hash_grant(
            payload["grant"], 31337, EXECUTOR
        ).hex()
        owner_signature = "0x" + sign_grant(
            payload["grant"], OWNER.key, 31337, EXECUTOR
        ).hex()
        bound = core.budget_bind(owner_signature)
        assert bound["status"] == "bound"
        _FakeRpc.current_allowance = 700_000
        with pytest.raises(ValueError, match="allowance"):
            core.allowance_verify(APPROVAL_TX)
        assert core.onboarding_status()["phase"] == "allowance"
        with pytest.raises(ValueError, match="onboarding"):
            core.snapshot()

        _FakeRpc.current_allowance = 1_000_000
        allowance = core.allowance_verify(APPROVAL_TX)
        assert allowance["allowance_id"]
        assert core.onboarding_status()["phase"] == "ready"
        snapshot = core.snapshot()
        assert snapshot["network"] == "eip155:31337"
        assert snapshot["receipt_signing_key"]
        assert core.network.gas_limit == 123_456
        assert core.network.max_gas_price_wei == 2_000_000_000

        with pytest.raises(ValueError, match="completed|signature"):
            core.budget_bind("0x" + "12" * 65)
        _FakeRpc.current_allowance = 700_000
        replay = core.allowance_verify(APPROVAL_TX)
        assert replay["allowance_id"] == allowance["allowance_id"]
        core.revoke()
        assert core.snapshot()["grant_status"] == "revoked"
    finally:
        core.close()


def test_restart_rejects_persisted_grant_with_changed_immutable_terms(core_factory, tmp_path):
    core = core_factory(tmp_path)
    try:
        wallet_challenge = core.wallet_challenge()
        core.wallet_verify(_sign_personal(wallet_challenge["message_to_sign"], OWNER))
        grant_challenge = core.grant_challenge()
        grant_result = core.grant_verify(
            _sign_personal(grant_challenge["message_to_sign"], OWNER)
        )
        grant = core.repository.spending_grant(grant_result["spending_grant_id"])
        core.repository.save_spending_grant(
            grant.model_copy(update={"max_amount_usdc": Decimal("2.00")})
        )
    finally:
        core.close()

    with pytest.raises(CorePersistenceError, match="grant|terms|scope"):
        core_factory(tmp_path)


def test_restart_recovers_exact_committed_grant_after_metadata_crash(core_factory, tmp_path):
    core = core_factory(tmp_path)
    try:
        wallet_challenge = core.wallet_challenge()
        core.wallet_verify(_sign_personal(wallet_challenge["message_to_sign"], OWNER))
        grant_challenge = core.grant_challenge()
        challenge_session = core.repository.account_session(grant_challenge["session_id"])
        expected_grant_id = challenge_session.payload["spending_grant_id"]

        persist_calls = 0
        persist_state = core._persist_state

        def crash_after_commit():
            nonlocal persist_calls
            persist_calls += 1
            if persist_calls == 2:
                raise RuntimeError("simulated metadata crash")
            persist_state()

        core._persist_state = crash_after_commit
        with pytest.raises(RuntimeError, match="metadata crash"):
            core.grant_verify(_sign_personal(grant_challenge["message_to_sign"], OWNER))
        assert len(core.repository.spending_grants("commerce-demo-user")) == 1
    finally:
        core.close()

    restored = core_factory(tmp_path)
    try:
        assert restored.grant.spending_grant_id == expected_grant_id
        assert restored.onboarding_status()["spending_grant_id"] == expected_grant_id
        assert len(restored.repository.spending_grants("commerce-demo-user")) == 1
    finally:
        restored.close()


def test_restart_reuses_identity_grant_and_binding_without_replacement(core_factory, tmp_path):
    core = core_factory(tmp_path)
    try:
        wallet_challenge = core.wallet_challenge()
        core.wallet_verify(_sign_personal(wallet_challenge["message_to_sign"], OWNER))
        grant_challenge = core.grant_challenge()
        grant = core.grant_verify(_sign_personal(grant_challenge["message_to_sign"], OWNER))
        payload = core.budget_payload()
        signature = "0x" + sign_grant(payload["grant"], OWNER.key, 31337, EXECUTOR).hex()
        binding = core.budget_bind(signature)
        identifiers = (core.wallet_identity.wallet_identity_id, grant["spending_grant_id"], binding["binding_id"])
    finally:
        core.close()

    restored = core_factory(tmp_path)
    try:
        assert restored.onboarding_status()["phase"] == "allowance"
        assert restored.wallet_identity.wallet_identity_id == identifiers[0]
        assert restored.grant.spending_grant_id == identifiers[1]
        assert restored.funding_service.get_budget_binding(spending_grant_id=identifiers[1]).binding_id == identifiers[2]
        metadata = json.loads((tmp_path / "public-onboarding.json").read_text())
        assert not any("key" in name.lower() for name in metadata)
    finally:
        restored.close()


def test_expired_or_revoked_grant_cannot_bind(core_factory, tmp_path):
    core = core_factory(tmp_path)
    try:
        wallet_challenge = core.wallet_challenge()
        core.wallet_verify(_sign_personal(wallet_challenge["message_to_sign"], OWNER))
        grant_challenge = core.grant_challenge()
        core.grant_verify(_sign_personal(grant_challenge["message_to_sign"], OWNER))
        signature = "0x" + sign_grant(
            core.budget_payload()["grant"], OWNER.key, 31337, EXECUTOR
        ).hex()
        core.revoke()
        with pytest.raises(ValueError, match="revoked|active"):
            core.budget_bind(signature)
        assert core.onboarding_status()["phase"] == "revoked"
    finally:
        core.close()
