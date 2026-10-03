"""Real HTTP merchant composition; settlement is explicit local Anvil."""
from decimal import Decimal
from pathlib import Path
from datetime import UTC,datetime
from agentonomy_commerce.runtime import ReviewRuntime, OFFERING_ID, RESOURCE
from examples.monad_commerce.core_bridge import CoreBridge
from shared.models import PaymentOption, Provider, ServiceOffering
import httpx
from examples.monad_commerce.merchant import BudgetMerchant
from agentonomy_commerce.transport import LoopbackMerchantTransport
from services.quote_service import QuoteService
from examples.monad_commerce.purchase_service import MonadPurchaseService

class BudgetCommerceRuntime(ReviewRuntime):
    def __init__(self,state_dir,merchant_port,bootstrap):
        super().__init__(state_dir,merchant_port)
        self.core=CoreBridge(self.state_dir,bootstrap)

    def _attach_settlement(self, payload):
        purchase = self.repository.get_purchase(payload['purchase_id'])
        if purchase and purchase.reservation_id:
            reservation = self.core.reservation(purchase.reservation_id)
            evidence = reservation.get('budget_watcher_evidence') or {}
            allowed = {'verified','status','transaction_hash','chain_id','executor','block_number',
                       'block_hash','finality_kind','finality_block_number','finality_block_hash','receipt_status','two_rpc_verified','amount_atomic','grant_hash','purchase_commitment',
                       'quote_commitment','owner','token','payee'}
            payload['settlement'] = {key:value for key,value in evidence.items() if key in allowed}
            if not payload['settlement']:
                payload['settlement'] = {'verified':False,'status':'pending','transaction_hash':reservation.get('tx_hash')}
        return payload

    def _execution_payload(self, result):
        return self._attach_settlement(super()._execution_payload(result))

    def purchase(self, purchase_id):
        return self._attach_settlement(super().purchase(purchase_id))
    def snapshot(self):
        self._require_started()
        raw=self.core.snapshot()
        allowed={'mode','simulation','real_funds','token_symbol','budget_usdc','used_amount_usdc',
            'reserved_amount_usdc','grant_status','chain_grant_id','grant_hash','network','token',
            'pay_to','executor','wallet_address','settlement_submissions'}
        result={key:raw[key] for key in allowed}
        result['remaining_amount_usdc']=format(Decimal(raw['budget_usdc'])-Decimal(raw['used_amount_usdc'])-Decimal(raw['reserved_amount_usdc']),'.2f')
        result['merchant_deliveries']=self.merchant.delivery_count
        result['service_transport']='http'
        result['risk_policy']='testnet_first_party_allowlist'
        return result

    def _register_catalog(self):
        provider = Provider(
            provider_id="commerce_analytics", name="Agentonomy CSV Reconciliation",
            domain="merchant.agentonomy.invalid", source="agentonomy_review", status="active",
            metadata={"settlement_mode": "local_anvil", "service_transport": "http"},
        )
        offering = ServiceOffering(
            offering_id=OFFERING_ID, provider_id=provider.provider_id, source="agentonomy_review",
            source_id="POST " + RESOURCE,
            name="CSV expense reconciliation", endpoint=RESOURCE, method="POST",
            description="Reconcile transaction CSV, remove exact duplicate records and return income, expense, "
                        "net and category totals. One currency per file; no financial advice.",
            input_schema={"type": "object", "required": ["csv_text"], "additionalProperties": False,
                          "properties": {"csv_text": {"type": "string", "maxLength": 131072}}},
            output_schema={"type": "object", "required": ["unique_transaction_count", "net_totals"]},
            tags=["csv", "reconciliation", "expense", "commerce"], status="verified",
            metadata={"trust_tier": "clink_verified", "verification_source": "first_party_review_service",
                      "settlement_mode": "local_anvil", "service_transport": "http"},
            payment_options=[PaymentOption(scheme="exact", network=self._network, asset=self._asset,
                                           amount_atomic="300000", pay_to=self._pay_to, price_usd="0.30",
                                           metadata={"token": "TestUSD", "simulation": False})],
        )
        self.repository.upsert_provider(provider)
        self.repository.upsert_offering(offering, verified_at=datetime.now(UTC))
        return provider, (offering,)

    def __enter__(self):
        try:
            self.core.__enter__()
            snapshot = self._core_snapshot_for_setup()
            self._network = snapshot["network"]
            self._asset = snapshot["token"]
            self._pay_to = snapshot["pay_to"]
            self._receipt_signing_key = snapshot["receipt_signing_key"]
            self.merchant = BudgetMerchant(
                self.state_dir / "merchant", receipt_secret=self._receipt_signing_key,
                network=self._network, token=self._asset, pay_to=self._pay_to,
                port=self.merchant_port, expected_input_hash=self._expected_input_hash,
                resource=RESOURCE,
            )
            self.merchant.__enter__()
            self.provider, self.offerings = self._register_catalog()
            self.quotes = QuoteService(self.repository, native_provider_ids={self.provider.provider_id})
            self._merchant_client = httpx.Client(
                transport=LoopbackMerchantTransport(RESOURCE, self.merchant.endpoint),
                timeout=15, trust_env=False, follow_redirects=False,
            )
            self.purchases = MonadPurchaseService(
                self.repository, self.core, client=self._merchant_client, ephemeral_store=self.store,
                native_provider_ids={self.provider.provider_id}, preview_ttl=300, proxy_context_ttl=900,
            )
            self._entered = True
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise
