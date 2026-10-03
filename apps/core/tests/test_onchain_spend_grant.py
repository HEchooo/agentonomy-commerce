"""Core budget-contract binding and settlement tests.

These tests intentionally use the real Core account/funding repositories and a
small injected backend.  No public RPC or signing key is used by the service.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

from eth_account import Account
from eth_utils import keccak
import pytest

# Import the shared module without changing the process-wide resolution of
# Core's `scripts` and `migrations` packages during the full test suite.
import importlib.util
protocol_path = Path(__file__).resolve().parents[2] / "facilitator" / "budget_protocol.py"
if "budget_protocol" not in sys.modules:
    protocol_spec = importlib.util.spec_from_file_location("budget_protocol", protocol_path)
    protocol_module = importlib.util.module_from_spec(protocol_spec)
    sys.modules["budget_protocol"] = protocol_module
    protocol_spec.loader.exec_module(protocol_module)
from budget_protocol import (
    PurchaseExecution,
    SpendGrant,
    encode_execute_calldata_hex,
    hash_execution,
    sign_execution,
    sign_grant,
)

from services.account_service.repository import AccountRepository, SpendingGrantRow
from services.account_service.schemas import AssetAllowance, SpendingGrant, WalletIdentity
from services.funding_service.budget_binding import derive_agent_scope
from services.funding_service.budget_service import BudgetFundingService
from services.funding_service.schemas import (
    CreateSpendingReservationRequest,
    ReleaseSpendingReservationRequest,
    SettleSpendingReservationRequest,
)
from services.funding_service.service import ReservationProvenance
from services.policy_service.budget_policy import BudgetPolicyService
from shared.budget_config import BudgetAppConfig


NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
NETWORK = "eip155:31337"
TOKEN = "0x" + "44" * 20
CONTRACT = "0x" + "99" * 20
PAYEE = "0x" + "22" * 20
OWNER = "0x19E7E376E7C213B7E7e7e46cc70A5dD086DAff2A"
OWNER_PRIVATE_KEY = "0x" + "11" * 32
EXECUTION_PRIVATE_KEY = "0x" + "22" * 32
RELAYER_PRIVATE_KEY = "0x" + "33" * 32
EXECUTION_SIGNER = Account.from_key(EXECUTION_PRIVATE_KEY).address.lower()
RELAYER = Account.from_key(RELAYER_PRIVATE_KEY).address


class SyntheticBudgetBackend:
    relayer_address = RELAYER
    execution_signer_address = EXECUTION_SIGNER

    def __init__(self) -> None:
        self.prepare_calls = []
        self.broadcast_calls = []
        self.verify_calls = []
        self._nonce = 7
        self._allowance = 10**18
        self._evidence = None
        self.broadcast_observer = None
        self.broadcast_error = None

    def prepare(self, row, binding, nonce):
        self.prepare_calls.append((row["reservation_id"], binding.binding_id, nonce))
        execution = PurchaseExecution(
            binding.grant_hash,
            "0x" + keccak(
                b"agentonomy:purchase:v1:" + row["purchase_id"].encode("utf-8")
            ).hex(),
            row["quote_hash"].lower(),
            int(row["amount_atomic"]),
            min(int(NOW.timestamp()) + 120, binding.valid_until),
        )
        execution_signature = sign_execution(
            execution,
            EXECUTION_PRIVATE_KEY,
            31337,
            CONTRACT,
        )
        data = encode_execute_calldata_hex(
            binding.grant,
            binding.owner_signature,
            execution,
            execution_signature,
        )
        transaction = {
            "chainId": 31337,
            "nonce": nonce,
            "from": self.relayer_address,
            "to": CONTRACT,
            "value": 0,
            "data": data,
            "gasPrice": 1,
            "gas": 100000,
        }
        signed = Account.sign_transaction(transaction, RELAYER_PRIVATE_KEY)
        return {
            "raw_transaction": "0x" + signed.raw_transaction.hex(),
            "tx_hash": "0x" + signed.hash.hex(),
            "relayer": self.relayer_address,
            "nonce": nonce,
            "transaction": transaction,
            "execution": execution.to_json(),
            "execution_digest": "0x"
            + hash_execution(execution, 31337, CONTRACT).hex(),
            "execution_signature": "0x" + execution_signature.hex(),
        }

    def broadcast(self, attempt):
        if self.broadcast_observer is not None:
            self.broadcast_observer(attempt)
        self.broadcast_calls.append(attempt)
        if self.broadcast_error is not None:
            raise self.broadcast_error
        return attempt["tx_hash"]

    def verify(self, row, binding, attempt):
        self.verify_calls.append((row["reservation_id"], binding.binding_id))
        return self._evidence

    def pending_nonce(self):
        return self._nonce

    def allowance(self, owner):
        assert owner.lower() == OWNER.lower()
        return self._allowance


def _service(tmp_path, backend=None):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'budget.sqlite3'}"
    now = NOW
    repository = AccountRepository(database_url)
    repository.save_wallet_identity(
        WalletIdentity(
            wallet_identity_id="wallet_1",
            user_id="user_1",
            wallet_address=OWNER,
            status="active",
            proof_hash="0xproof",
            verified_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    repository.save_spending_grant(
        SpendingGrant(
            spending_grant_id="grant_1",
            wallet_identity_id="wallet_1",
            user_id="user_1",
            agent_id="hermes",
            status="active",
            max_amount_usdc=Decimal("1"),
            per_transaction_limit_usdc=Decimal("0.25"),
            hourly_limit_usdc=Decimal("1"),
            daily_limit_usdc=Decimal("1"),
            product_scopes=["marketplace"],
            venue_scopes=["clink_marketplace"],
            merchant_scopes=["merchant_1"],
            merchant_trust_scopes=["registry_verified"],
            notification_mode="silent_under_limits",
            network_scopes=[NETWORK],
            asset_scopes=[TOKEN],
            starts_at=datetime(2026, 10, 1, tzinfo=UTC),
            expires_at=datetime(2026, 10, 10, tzinfo=UTC),
            created_at=now,
            updated_at=now,
        )
    )
    repository.save_asset_allowance(
        AssetAllowance(
            asset_allowance_id="allowance_1",
            wallet_identity_id="wallet_1",
            network=NETWORK,
            token_address=TOKEN,
            token_symbol="TestUSD",
            token_decimals=6,
            spender_address=CONTRACT,
            approved_amount_atomic=1_000_000,
            observed_allowance_atomic=1_000_000,
            status="active",
            confirmed_block=1,
            last_chain_check_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    config = BudgetAppConfig(
        funding_database_url=database_url,
        budget_network=NETWORK,
        budget_chain_id=31337,
        budget_token_address=TOKEN,
        budget_token_symbol="TestUSD",
        budget_token_decimals=6,
        budget_contract_address=CONTRACT,
        budget_payee_address=PAYEE,
        budget_allowed_merchant_ids=("merchant_1",),
        budget_allowed_resources=("https://merchant.example/api",),
        clink_receipt_signing_key="budget-receipt-signing-key-012345678901234567890",
    )
    backend = backend or SyntheticBudgetBackend()
    service = BudgetFundingService(
        config=config,
        storage_file=tmp_path / "funding.jsonl",
        policy_service=BudgetPolicyService(config=config),
        backend=backend,
    )
    service._utc_now = lambda: NOW.replace(tzinfo=None)
    return repository, service, backend


def _request() -> CreateSpendingReservationRequest:
    return CreateSpendingReservationRequest(
        purchase_id="purchase_1",
        idempotency_key="purchase_1",
        wallet_identity_id="wallet_1",
        spending_grant_id="grant_1",
        asset_allowance_id="allowance_1",
        product="marketplace",
        action_id="action_1",
        policy_decision_id="policy_1",
        merchant_id="merchant_1",
        merchant_trust_tier="registry_verified",
        quote_hash="0x" + "dd" * 32,
        amount_usdc="0.25",
        amount_atomic="250000",
        network=NETWORK,
        asset=TOKEN,
        destination=PAYEE,
        resource="https://merchant.example/api",
        venue="clink_marketplace",
    )


def _signed_grant() -> tuple[SpendGrant, str]:
    return _signed_grant_for("grant_1")


def _signed_grant_for(spending_grant_id: str, *, grant_id: str = "0x" + "aa" * 32) -> tuple[SpendGrant, str]:
    grant = SpendGrant.from_json(
        {
            "grantId": grant_id,
            "owner": OWNER,
            "agentScope": derive_agent_scope("hermes", spending_grant_id),
            "token": TOKEN,
            "payee": PAYEE,
            "maxPerPayment": "250000",
            "maxTotal": "1000000",
            "validAfter": str(int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())),
            "validUntil": str(int(datetime(2026, 10, 10, tzinfo=UTC).timestamp())),
            "executionSigner": EXECUTION_SIGNER,
        }
    )
    signature = sign_grant(grant, OWNER_PRIVATE_KEY, 31337, CONTRACT)
    return grant, "0x" + signature.hex()


def _bind(service: BudgetFundingService):
    grant, signature = _signed_grant()
    return service.bind_budget_grant(
        spending_grant_id="grant_1",
        wallet_identity_id="wallet_1",
        grant=grant,
        owner_signature=signature,
    )


def _save_second_core_grant(repository: AccountRepository) -> None:
    repository.save_spending_grant(
        SpendingGrant(
            spending_grant_id="grant_2",
            wallet_identity_id="wallet_1",
            user_id="user_1",
            agent_id="hermes",
            status="active",
            max_amount_usdc=Decimal("1"),
            per_transaction_limit_usdc=Decimal("0.25"),
            hourly_limit_usdc=Decimal("1"),
            daily_limit_usdc=Decimal("1"),
            product_scopes=["marketplace"],
            venue_scopes=["clink_marketplace"],
            merchant_scopes=["merchant_1"],
            merchant_trust_scopes=["registry_verified"],
            notification_mode="silent_under_limits",
            network_scopes=[NETWORK],
            asset_scopes=[TOKEN],
            starts_at=datetime(2026, 10, 1, tzinfo=UTC),
            expires_at=datetime(2026, 10, 10, tzinfo=UTC),
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _verified_evidence(binding, *, status="verified") -> dict:
    transaction_hash = SyntheticBudgetBackend().prepare(
        {
            "reservation_id": "template",
            "purchase_id": "purchase_1",
            "quote_hash": "0x" + "dd" * 32,
            "amount_atomic": "250000",
        },
        binding,
        7,
    )["tx_hash"]
    return {
        "verified": True,
        "status": status,
        "chain_id": 31337,
        "executor": CONTRACT,
        "grant_hash": binding.grant_hash,
        "owner": OWNER.lower(),
        "token": TOKEN,
        "payee": PAYEE,
        "amount_atomic": "250000",
        "purchase_id": "purchase_1",
        "quote_hash": "0x" + "dd" * 32,
        "block_number": 42,
        "block_hash": "0x" + "12" * 32,
        "finality_kind": "local",
        "finality_block_number": 100,
        "finality_block_hash": "0x" + "13" * 32,
        "receipt_status": 0 if status == "reverted" else 1,
        "two_rpc_verified": True,
        "transaction_hash": transaction_hash,
    }


def test_binding_rejects_owner_signature_or_scope_mismatch(tmp_path):
    _repository, service, _backend = _service(tmp_path)
    grant = {
        "grantId": "0x" + "aa" * 32,
        "owner": OWNER,
        "agentScope": derive_agent_scope("hermes", "grant_1"),
        "token": TOKEN,
        "payee": PAYEE,
        "maxPerPayment": "250000",
        "maxTotal": "1000000",
        "validAfter": str(int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())),
        "validUntil": str(int(datetime(2026, 10, 10, tzinfo=UTC).timestamp())),
        "executionSigner": EXECUTION_SIGNER,
    }
    with pytest.raises(ValueError, match="signature|owner"):
        service.bind_budget_grant(
            spending_grant_id="grant_1",
            wallet_identity_id="wallet_1",
            grant=grant,
            owner_signature="0x" + "00" * 65,
        )


def test_identical_binding_replay_keeps_original_creation_time(tmp_path):
    _repository, service, _backend = _service(tmp_path)
    first = _bind(service)
    service._utc_now = lambda: NOW.replace(day=4, tzinfo=None)
    second = _bind(service)

    assert second.to_dict() == first.to_dict()


def test_binding_rejects_chain_grant_identity_collision(tmp_path):
    repository, service, _backend = _service(tmp_path)
    _bind(service)
    _save_second_core_grant(repository)
    grant, signature = _signed_grant_for("grant_2")

    with pytest.raises(ValueError, match="chain grant identity"):
        service.bind_budget_grant(
            spending_grant_id="grant_2",
            wallet_identity_id="wallet_1",
            grant=grant,
            owner_signature=signature,
        )


def test_budget_contract_success_settles_core_once(tmp_path):
    repository, service, backend = _service(tmp_path)
    row = _request()
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
        reservation = service.reserve_spending(row)
    assert reservation["budget_accounting_state"] == "reserved"
    assert service.get_reservation(reservation["reservation_id"])["state"] == "spending_reserved"
    assert reservation["settlement_rail"] == "budget_contract"

    binding = _bind(service)
    backend._evidence = _verified_evidence(binding)
    settle_request = SettleSpendingReservationRequest(payment_authorization={})
    malicious_request = SettleSpendingReservationRequest(
        payment_authorization={"rail": "native_allowance"}
    )
    with pytest.raises(ValueError, match="trusted budget service"):
        service.settle_reservation(reservation["reservation_id"], malicious_request)
    with patch.object(
        service,
        "_require_reservation_policy_controls",
        return_value=NOW.replace(tzinfo=None),
    ):
        settled = service.settle_reservation(reservation["reservation_id"], settle_request)

    assert settled["state"] == "settled"
    assert settled["budget_accounting_state"] == "settled"
    assert settled["settlement_rail"] == "budget_contract"
    assert len(backend.prepare_calls) == 1
    assert len(backend.broadcast_calls) == 1
    assert len(backend.verify_calls) == 1


def test_tampered_signed_attempt_is_rejected_before_journal(tmp_path):
    repository, service, backend = _service(tmp_path)
    row = _request()
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
        reservation = service.reserve_spending(row)
    _bind(service)
    original_prepare = backend.prepare

    def tampered_prepare(row, binding, nonce):
        attempt = original_prepare(row, binding, nonce)
        return {**attempt, "tx_hash": "0x" + "00" * 32}

    backend.prepare = tampered_prepare
    with patch.object(
        service,
        "_require_reservation_policy_controls",
        return_value=NOW.replace(tzinfo=None),
    ):
        with pytest.raises(ValueError, match="hash"):
            service.settle_reservation(
                reservation["reservation_id"],
                SettleSpendingReservationRequest(payment_authorization={}),
            )
    assert service.get_reservation(reservation["reservation_id"])["state"] == "spending_reserved"
    assert backend.broadcast_calls == []

    public_row = service.get_reservation(reservation["reservation_id"])
    assert public_row is not None
    assert "budget_attempt" not in public_row

    with repository.sessions() as session:
        grant_row = session.get(SpendingGrantRow, "grant_1")
        assert grant_row is not None
        assert grant_row.used_amount_usdc == Decimal("0")
        assert grant_row.reserved_amount_usdc == Decimal("0.25")


def test_public_reservation_redacts_signed_budget_attempt(tmp_path):
    repository, service, _backend = _service(tmp_path)
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
    _bind(service)

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
                service.settle_reservation(
                    reservation["reservation_id"],
                    SettleSpendingReservationRequest(payment_authorization={}),
                )

    public = service.get_reservation(reservation["reservation_id"])
    assert public is not None
    assert public["budget_attempt"] == {
        "tx_hash": public["tx_hash"],
        "relayer": RELAYER.lower(),
        "nonce": 7,
        "execution_digest": public["budget_attempt"]["execution_digest"],
    }
    assert "raw_transaction" not in public["budget_attempt"]
    assert "execution_signature" not in public["budget_attempt"]
    assert "transaction" not in public["budget_attempt"]
    assert "execution" not in public["budget_attempt"]
    assert "owner_signature" not in public["budget_attempt"]


def test_budget_readiness_uses_native_gateway_shape_without_hosted_advertising(tmp_path):
    _repository, service, _backend = _service(tmp_path)

    readiness = service.get_funding_readiness()

    assert readiness["status"] == "ready"
    assert readiness["settlement_rail"] == "budget_contract"
    assert readiness["live_funding_enabled"] is True
    assert readiness["native_facilitator_enabled"] is True
    assert readiness["native_facilitator_ready"] is True
    assert readiness["hosted_facilitator_enabled"] is False
    assert readiness["hosted_facilitator_ready"] is False
    assert readiness["supported_assets"] == {NETWORK: TOKEN}
    assert readiness["spender_address"] == CONTRACT
    assert readiness["spender_addresses"] == {NETWORK: CONTRACT}


def test_concurrent_core_grants_cannot_share_one_chain_grant(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    repository, service, _backend = _service(tmp_path)
    _save_second_core_grant(repository)
    start = Barrier(2)

    def bind(grant_id):
        grant, signature = _signed_grant_for(grant_id)
        start.wait(timeout=5)
        try:
            return service.bind_budget_grant(
                spending_grant_id=grant_id, wallet_identity_id='wallet_1',
                grant=grant, owner_signature=signature,
            )
        except ValueError as exc:
            assert 'chain grant identity' in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(bind, ('grant_1', 'grant_2')))
    assert sum(result is not None for result in results) == 1
    assert sum(service.get_budget_binding(spending_grant_id=key) is not None
               for key in ('grant_1', 'grant_2')) == 1
