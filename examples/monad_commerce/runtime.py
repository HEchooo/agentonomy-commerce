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
from agentonomy_commerce.service_pricing import atomic_price_terms, format_token_amount


_DEFAULT_SERVICE_AMOUNT_ATOMIC = 300000
_MAX_SERVICE_AMOUNT_ATOMIC = 500000

class BudgetCommerceRuntime(ReviewRuntime):
    def __init__(self,state_dir,merchant_port,bootstrap):
        self._configure_service_price(bootstrap)
        super().__init__(state_dir,merchant_port)
        self.core=CoreBridge(self.state_dir,bootstrap)

    def _configure_service_price(self, bootstrap):
        """Bind the catalog price to the validated signer scope.

        Local bootstrap has no signer configuration and retains the historical
        0.30 TestUSD default. Hosted/public bootstrap carries the current
        amount in the protected signer scope; no top-level or CLI override is
        accepted here.
        """

        scope = {}
        if isinstance(bootstrap, dict):
            signer_configuration = bootstrap.get("signer_configuration")
            if isinstance(signer_configuration, dict):
                candidate = signer_configuration.get("scope")
                if isinstance(candidate, dict):
                    scope = candidate
        amount_atomic = scope.get("service_amount_atomic", _DEFAULT_SERVICE_AMOUNT_ATOMIC)
        terms = atomic_price_terms(amount_atomic)
        amount_value = int(terms["amount_atomic"])
        if amount_value > _MAX_SERVICE_AMOUNT_ATOMIC:
            raise ValueError("service price exceeds the signed per-payment limit")
        self._service_payment_terms = terms

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
        for key in ('budget_usdc', 'used_amount_usdc', 'reserved_amount_usdc'):
            result[key] = format_token_amount(result[key])
        result['remaining_amount_usdc']=format_token_amount(
            Decimal(raw['budget_usdc'])
            - Decimal(raw['used_amount_usdc'])
            - Decimal(raw['reserved_amount_usdc'])
        )
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
                                           amount_atomic=self._service_payment_terms["amount_atomic"],
                                           pay_to=self._pay_to,
                                           price_usd=self._service_payment_terms["price_usd"],
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
                expected_payment=self._expected_payment,
                resource=RESOURCE,
                settlement_mode=snapshot['mode'],
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
