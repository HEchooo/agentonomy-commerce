from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agentonomy_commerce.storage import SQLiteStore
from examples.monad_commerce.purchase_service import MonadPurchaseService
from examples.monad_commerce.public_runtime import PublicCommerceRuntime
from services.purchase_service import PurchaseService, digest
from shared.commerce import Purchase, PurchasePreview


CSV = (
    "transaction_id,date,description,amount,currency,category\n"
    "1,2026-10-03,Sale,10.00,USD,sales\n"
    "2,2026-10-03,Fee,-2.00,USD,fees\n"
)
RESOURCE = "https://merchant.agentonomy.invalid/v1/reconcile"
TX_HASH = "0x" + "a" * 64
SERVICE_INPUT = {"csv_text": CSV}
PAYMENT = {
    "scheme": "exact",
    "network": "monad-testnet",
    "asset": "0x" + "1" * 40,
    "amount_atomic": "300000",
    "pay_to": "0x" + "2" * 40,
    "price_usd": "0.30",
    "metadata": {"token": "TestUSD"},
}


def _preview(now: datetime) -> PurchasePreview:
    return PurchasePreview(
        preview_id="preview_recovery",
        offering_id="csv-reconciliation-v1",
        user_id="commerce-demo-user",
        quote_hash=digest(PAYMENT),
        input_hash=digest(SERVICE_INPUT),
        payment=PAYMENT,
        execution_mode="clink_allowance",
        payment_capability={"rail": "clink_allowance"},
        expires_at=now + timedelta(seconds=300),
        created_at=now,
    )


def _purchase(preview: PurchasePreview, state="payment_submitted") -> Purchase:
    return Purchase(
        purchase_id="purchase_recovery",
        preview_id=preview.preview_id,
        offering_id=preview.offering_id,
        user_id=preview.user_id,
        state=state,
        execution_mode="clink_allowance",
        input_hash=preview.input_hash,
        reservation_id="reservation_recovery",
        receipt_id="receipt_recovery" if state == "paid_but_undelivered" else None,
        created_at=preview.created_at,
        updated_at=preview.created_at,
    )


class FakeRepository:
    def __init__(self, preview, purchase=None):
        self.preview = preview
        self.purchase = purchase
        self.offering = SimpleNamespace(
            provider_id="commerce_analytics",
            source="agentonomy_review",
            method="POST",
            endpoint=RESOURCE,
            metadata={"trust_tier": "clink_verified"},
            payment_options=(SimpleNamespace(model_dump=lambda **_: PAYMENT),),
        )

    def get_preview(self, preview_id):
        return self.preview if preview_id == self.preview.preview_id else None

    def get_purchase(self, purchase_id):
        return self.purchase if purchase_id == "purchase_recovery" else None

    def active_offering(self, offering_id):
        return self.offering if offering_id == self.preview.offering_id else None

    def claim_purchase_execution(self, purchase, **_kwargs):
        if self.purchase is not None:
            if self.purchase.state not in _kwargs["allowed_states"]:
                return self.purchase, None
            return self.purchase, "claim-token"
        self.purchase = purchase
        return purchase, "claim-token"

    def save_claimed_purchase(self, purchase, *_args, **_kwargs):
        self.purchase = purchase

    def save_purchase_with_reputation_event(self, purchase, *_args, **_kwargs):
        self.purchase = purchase

    def complete_finalization_for_purchase(self, _purchase_id):
        return None


class FakeCore:
    def __init__(self, settlement=None):
        base = {
            "reservation_id": "reservation_recovery",
            "purchase_id": "purchase_recovery",
            "user_id": "commerce-demo-user",
            "quote_hash": digest(PAYMENT),
            "amount_atomic": PAYMENT["amount_atomic"],
            "network": PAYMENT["network"],
            "token_address": PAYMENT["asset"],
            "destination": PAYMENT["pay_to"],
            "resource": RESOURCE,
            "merchant_id": "commerce_analytics",
            "settlement_rail": "budget_contract",
            "state": "settled",
            "receipt_id": "receipt_recovery",
            "tx_hash": TX_HASH,
            "receipt": {
                "receipt_id": "receipt_recovery",
                "tx_hash": TX_HASH,
                "user_id": "commerce-demo-user",
                "status": "settled",
                "chain": PAYMENT["network"],
                "token": "TestUSD",
                "token_address": PAYMENT["asset"],
                "destination": PAYMENT["pay_to"],
                "resource": RESOURCE,
                "amount_usdc": "0.3",
                "metadata": {
                    "purchase_id": "purchase_recovery",
                    "reservation_id": "reservation_recovery",
                    "quote_hash": digest(PAYMENT),
                    "merchant_id": "commerce_analytics",
                    "amount_atomic": PAYMENT["amount_atomic"],
                    "receipt_scope": {
                        "purchase_id": "purchase_recovery",
                        "reservation_id": "reservation_recovery",
                        "quote_hash": digest(PAYMENT),
                        "user_id": "commerce-demo-user",
                        "tx_hash": TX_HASH,
                        "amount_atomic": PAYMENT["amount_atomic"],
                    },
                    "receipt_signature": "sha256=fixture",
                },
            },
            "budget_watcher_evidence": {
                "verified": True,
                "status": "verified",
                "receipt_status": 1,
                "two_rpc_verified": True,
                "transaction_hash": TX_HASH,
                "tx_hash": TX_HASH,
                "purchase_id": "purchase_recovery",
                "quote_hash": digest(PAYMENT),
                "amount_atomic": PAYMENT["amount_atomic"],
                "chain_id": 10143,
                "executor": "0x" + "3" * 40,
                "token": PAYMENT["asset"],
                "payee": PAYMENT["pay_to"],
                "owner": "0x" + "4" * 40,
                "grant_hash": "0x" + "5" * 64,
                "block_number": 1,
                "block_hash": "0x" + "6" * 64,
                "finality_kind": "monad_verified",
                "finality_block_number": 1,
                "finality_block_hash": "0x" + "6" * 64,
            },
        }
        self.settlement = dict(base)
        if settlement:
            self.settlement.update(settlement)
            if isinstance(settlement.get("receipt"), dict):
                self.settlement["receipt"] = {
                    **base["receipt"],
                    **settlement["receipt"],
                }
            if isinstance(settlement.get("budget_watcher_evidence"), dict):
                self.settlement["budget_watcher_evidence"] = {
                    **base["budget_watcher_evidence"],
                    **settlement["budget_watcher_evidence"],
                }
        self.reserve_calls = 0
        self.settle_calls = 0
        self.reconcile_calls = 0
        self.finalize_calls = 0

    def reservation(self, _reservation_id):
        return self.settlement

    def reconcile(self, _reservation_id):
        self.reconcile_calls += 1
        return self.settlement

    def reserve(self, *_args, **_kwargs):
        self.reserve_calls += 1
        raise AssertionError("recovery must not reserve")

    def settle(self, *_args, **_kwargs):
        self.settle_calls += 1
        raise AssertionError("recovery must not settle")

    def finalize(self, *_args, **_kwargs):
        self.finalize_calls += 1
        return {"state": "finalized"}


class MerchantResponse:
    is_success = True
    headers = {"content-type": "application/json"}

    def json(self):
        return {"unique_transaction_count": 2, "net_totals": {"USD": "8.00"}}


class Merchant:
    def __init__(self):
        self.calls = []

    def request(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint, kwargs))
        return MerchantResponse()


def _service(tmp_path, now, repository, core, merchant=None):
    store = SQLiteStore(
        tmp_path / "review-values.sqlite3",
        clock=lambda: now[0],
    )
    return MonadPurchaseService(
        repository,
        core,
        client=merchant or Merchant(),
        ephemeral_store=store,
        native_provider_ids={"commerce_analytics"},
    )


def test_paid_input_survives_preview_expiry_and_restart_without_new_payment(
    tmp_path, monkeypatch
):
    now = [datetime.now(UTC).timestamp()]
    created = datetime.fromtimestamp(now[0], UTC)
    preview = _preview(created)
    repository = FakeRepository(preview)
    core = FakeCore()
    merchant = Merchant()
    service = _service(tmp_path, now, repository, core, merchant)
    service.inputs.set(preview.preview_id, SERVICE_INPUT, service.preview_ttl)

    def crashed(*_args, **_kwargs):
        raise TimeoutError("simulated Marketplace crash after staging")

    monkeypatch.setattr(PurchaseService, "execute", crashed)
    with pytest.raises(TimeoutError):
        service.execute(preview.preview_id)
    monkeypatch.undo()

    now[0] += 301
    repository.purchase = _purchase(preview)
    restarted = _service(tmp_path, now, repository, core, merchant)
    result = restarted.execute(preview.preview_id)

    assert result["purchase"].state == "delivered"
    assert result["purchase"].input_hash == preview.input_hash
    assert merchant.calls[0][2]["json"] == SERVICE_INPUT
    assert core.reserve_calls == core.settle_calls == 0
    assert core.reconcile_calls == 1


def test_paid_input_recovery_key_expires_after_24_hours(tmp_path, monkeypatch):
    now = [datetime.now(UTC).timestamp()]
    created = datetime.fromtimestamp(now[0], UTC)
    preview = _preview(created)
    repository = FakeRepository(preview)
    core = FakeCore()
    merchant = Merchant()
    service = _service(tmp_path, now, repository, core, merchant)
    service.inputs.set(preview.preview_id, SERVICE_INPUT, service.preview_ttl)

    monkeypatch.setattr(PurchaseService, "execute", lambda *_args, **_kwargs: None)
    service.execute(preview.preview_id)
    monkeypatch.undo()
    now[0] += service._PAID_INPUT_TTL_SECONDS + 1
    repository.purchase = _purchase(preview)

    restarted = _service(tmp_path, now, repository, core, merchant)
    result = restarted.execute(preview.preview_id)

    assert result["purchase"].state == "paid_but_undelivered"
    assert result["purchase"].reason_code == "INPUT_UNAVAILABLE_AFTER_PAYMENT"
    assert merchant.calls == []
    assert core.reserve_calls == core.settle_calls == 0


def test_expired_unpaid_preview_does_not_stage_input_or_pay(tmp_path):
    now = [datetime.now(UTC).timestamp()]
    created = datetime.fromtimestamp(now[0] - 301, UTC)
    now[0] = created.timestamp() + 301
    preview = _preview(created)
    repository = FakeRepository(preview)
    core = FakeCore()
    service = _service(tmp_path, now, repository, core)

    result = service.execute(preview.preview_id)

    assert result["purchase"].state == "failed"
    assert result["purchase"].reason_code == "PREVIEW_EXPIRED"
    assert service.inputs.get(service._paid_input_key(preview.preview_id)) is None
    assert core.reserve_calls == core.settle_calls == 0


class RecoveryPurchases:
    def __init__(self, result):
        self.result = result
        self.restore_calls = []
        self.offering = SimpleNamespace(provider_id="commerce_analytics", endpoint=RESOURCE)

    def restore_paid_input(self, purchase, preview, service_input, settlement):
        if digest(service_input) != purchase.input_hash:
            raise ValueError("purchase input hash mismatch")
        if not MonadPurchaseService._verified_settlement_for_purchase(
            purchase,
            settlement,
            preview=preview,
            offering=self.offering,
        ):
            raise ValueError("verified settled payment proof is required")
        self.restore_calls.append((purchase, preview, service_input, settlement))


class RecoveryRuntimeCore(FakeCore):
    pass


def _public_runtime(preview, purchase, core=None):
    runtime = PublicCommerceRuntime.__new__(PublicCommerceRuntime)
    runtime.repository = FakeRepository(preview, purchase)
    runtime.core = core or RecoveryRuntimeCore()
    scope = {
        "purchase_id": purchase.purchase_id,
        "user_id": purchase.user_id,
        "quote_hash": preview.quote_hash,
        "input_hash": preview.input_hash,
        "offering_id": purchase.offering_id,
        "reservation_id": purchase.reservation_id,
    }
    runtime.core.settlement = {**scope, **runtime.core.settlement}
    runtime.purchases = RecoveryPurchases({"state": "restored"})
    runtime.execute_calls = []

    def execute(preview_id):
        runtime.execute_calls.append(preview_id)
        return runtime.purchases.result

    runtime.execute = execute
    return runtime


def test_recover_with_original_csv_rehydrates_paid_order_without_payment():
    created = datetime.now(UTC)
    preview = _preview(created)
    purchase = _purchase(preview, state="paid_but_undelivered")
    core = RecoveryRuntimeCore()
    runtime = _public_runtime(preview, purchase, core)

    result = runtime.recover_purchase(purchase.purchase_id, csv_text=CSV)

    assert result == {"state": "restored"}
    assert runtime.execute_calls == [preview.preview_id]
    assert len(runtime.purchases.restore_calls) == 1
    assert runtime.purchases.restore_calls[0][2] == SERVICE_INPUT
    assert core.reconcile_calls == 1
    assert core.reserve_calls == core.settle_calls == 0


@pytest.mark.parametrize(
    "mismatch",
    ["identity", "hash"],
)
def test_recover_rejects_original_identity_or_hash_mismatch_without_writes(mismatch):
    created = datetime.now(UTC)
    original_preview = _preview(created)
    original_purchase = _purchase(original_preview, state="paid_but_undelivered")
    preview = (
        original_preview.model_copy(update={"user_id": "other-user"})
        if mismatch == "identity"
        else original_preview
    )
    purchase = (
        original_purchase.model_copy(
            update={"input_hash": digest({"csv_text": "different"})}
        )
        if mismatch == "hash"
        else original_purchase
    )
    core = RecoveryRuntimeCore()
    runtime = _public_runtime(preview, purchase, core)

    with pytest.raises(ValueError):
        runtime.recover_purchase(purchase.purchase_id, csv_text=CSV)

    assert runtime.purchases.restore_calls == []
    assert runtime.execute_calls == []
    assert core.reconcile_calls == 0


def test_recover_rejects_wrong_csv_without_recovery_write():
    created = datetime.now(UTC)
    preview = _preview(created)
    purchase = _purchase(preview, state="paid_but_undelivered")
    core = RecoveryRuntimeCore()
    runtime = _public_runtime(preview, purchase, core)

    with pytest.raises(ValueError, match="purchase input hash mismatch"):
        runtime.recover_purchase(purchase.purchase_id, csv_text="different")

    assert runtime.purchases.restore_calls == []
    assert runtime.execute_calls == []
    assert core.reconcile_calls == 1


def test_recover_rejects_missing_verified_payment_without_writes():
    created = datetime.now(UTC)
    preview = _preview(created)
    purchase = _purchase(preview, state="paid_but_undelivered")
    core = RecoveryRuntimeCore(
        settlement={
            "state": "settled",
            "tx_hash": "0x" + "a" * 64,
            "receipt": {"status": 1},
            "budget_watcher_evidence": {"verified": False},
        }
    )
    runtime = _public_runtime(preview, purchase, core)

    with pytest.raises(ValueError):
        runtime.recover_purchase(purchase.purchase_id, csv_text=CSV)

    assert runtime.purchases.restore_calls == []
    assert runtime.execute_calls == []
    assert core.reconcile_calls == 1


def test_recover_rejects_core_scope_mismatch_before_recovery_write():
    created = datetime.now(UTC)
    preview = _preview(created)
    purchase = _purchase(preview, state="paid_but_undelivered")
    core = RecoveryRuntimeCore()
    runtime = _public_runtime(preview, purchase, core)
    runtime.core.settlement["quote_hash"] = "0x" + "f" * 64

    with pytest.raises(ValueError, match="Core reservation scope mismatch"):
        runtime.recover_purchase(purchase.purchase_id, csv_text=CSV)

    assert runtime.purchases.restore_calls == []
    assert runtime.execute_calls == []
    assert core.reconcile_calls == 0


@pytest.mark.parametrize("missing_field", ["purchase_id", "user_id", "tx_hash", "receipt_id"])
def test_recover_rejects_incomplete_core_scope_before_recovery_write(missing_field):
    created = datetime.now(UTC)
    preview = _preview(created)
    purchase = _purchase(preview, state="paid_but_undelivered")
    runtime = _public_runtime(preview, purchase, RecoveryRuntimeCore())
    del runtime.core.settlement[missing_field]

    with pytest.raises(ValueError, match="Core reservation scope mismatch"):
        runtime.recover_purchase(purchase.purchase_id, csv_text=CSV)

    assert runtime.purchases.restore_calls == []
    assert runtime.execute_calls == []
    assert runtime.core.reconcile_calls == 0


@pytest.mark.parametrize("receipt_field", ["transaction_hash", "tx_hash"])
def test_recover_rejects_receipt_bound_to_another_transaction(receipt_field):
    created = datetime.now(UTC)
    preview = _preview(created)
    purchase = _purchase(preview, state="paid_but_undelivered")
    core = RecoveryRuntimeCore()
    core.settlement["receipt"] = {
        receipt_field: "0x" + "b" * 64,
        "status": 1,
    }
    runtime = _public_runtime(preview, purchase, core)

    with pytest.raises(ValueError, match="verified settled payment proof"):
        runtime.recover_purchase(purchase.purchase_id, csv_text=CSV)

    assert runtime.purchases.restore_calls == []
    assert runtime.execute_calls == []
    assert core.reconcile_calls == 1


def test_recover_without_csv_keeps_existing_empty_body_compatibility():
    created = datetime.now(UTC)
    preview = _preview(created)
    purchase = _purchase(preview, state="paid_but_undelivered")
    runtime = _public_runtime(preview, purchase)

    result = runtime.recover_purchase(purchase.purchase_id)

    assert result == {"state": "restored"}
    assert runtime.execute_calls == [preview.preview_id]
    assert runtime.purchases.restore_calls == []


def test_restore_paid_input_persists_only_verified_original_input(tmp_path):
    now = [datetime.now(UTC).timestamp()]
    preview = _preview(datetime.fromtimestamp(now[0], UTC))
    purchase = _purchase(preview, state="paid_but_undelivered")
    core = FakeCore()
    service = _service(tmp_path, now, FakeRepository(preview, purchase), core)

    service.restore_paid_input(purchase, preview, SERVICE_INPUT, core.settlement)

    assert service.inputs.get(service._paid_input_key(preview.preview_id)) == SERVICE_INPUT


def test_restore_paid_input_rejects_receipt_amount_mismatch(tmp_path):
    now = [datetime.now(UTC).timestamp()]
    preview = _preview(datetime.fromtimestamp(now[0], UTC))
    purchase = _purchase(preview, state="paid_but_undelivered")
    core = FakeCore()
    core.settlement["receipt"]["amount_usdc"] = "0.31"
    service = _service(tmp_path, now, FakeRepository(preview, purchase), core)

    with pytest.raises(ValueError, match="verified settled payment proof"):
        service.restore_paid_input(purchase, preview, SERVICE_INPUT, core.settlement)

    assert service.inputs.get(service._paid_input_key(preview.preview_id)) is None
