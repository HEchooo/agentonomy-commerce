"""Public testnet settlement with the existing first-party HTTP merchant."""
from agentonomy_commerce.runtime import ReviewRuntime
from examples.monad_commerce.runtime import BudgetCommerceRuntime
from examples.monad_commerce.public_bridge import PublicCoreBridge


class PublicCommerceRuntime(BudgetCommerceRuntime):
    def __init__(self, state_dir, merchant_port, bootstrap):
        ReviewRuntime.__init__(self, state_dir, merchant_port)
        self.core = PublicCoreBridge(self.state_dir, bootstrap)

    def onboarding_status(self):
        return self.core.onboarding('onboarding_status')

    def _core_scope_matches(self, purchase, preview, reservation):
        if not isinstance(reservation, dict):
            return False
        offering = self.repository.active_offering(purchase.offering_id)
        if offering is None:
            return False
        expected = {
            'reservation_id': purchase.reservation_id,
            'purchase_id': purchase.purchase_id,
            'user_id': purchase.user_id,
            'quote_hash': preview.quote_hash,
            'amount_atomic': preview.payment['amount_atomic'],
            'network': preview.payment['network'],
            'token_address': preview.payment['asset'],
            'destination': preview.payment['pay_to'],
            'resource': offering.endpoint,
            'merchant_id': offering.provider_id,
            'settlement_rail': 'budget_contract',
        }
        required = (*expected, 'tx_hash', 'receipt_id')
        if any(reservation.get(field) in {None, ''} for field in required):
            return False
        if any(reservation.get(field) != expected[field] for field in expected):
            return False
        if purchase.receipt_id and purchase.receipt_id not in {
            reservation['receipt_id'],
            reservation['tx_hash'],
        }:
            return False
        for field, expected_value in {
            'offering_id': purchase.offering_id,
            'input_hash': preview.input_hash,
        }.items():
            if field in reservation and reservation[field] != expected_value:
                return False
        return True

    def recover_purchase(self, purchase_id, csv_text=None):
        purchase = self.repository.get_purchase(purchase_id)
        if purchase is None:
            raise KeyError('purchase not found')
        if csv_text is None:
            return self.execute(purchase.preview_id)
        if not isinstance(csv_text, str) or not 1 <= len(csv_text) <= 131072:
            raise ValueError('csv_text must be between 1 and 131072 characters')
        preview = self.repository.get_preview(purchase.preview_id)
        if preview is None:
            raise ValueError('purchase preview not found')
        if (
            purchase.preview_id != preview.preview_id
            or purchase.user_id != preview.user_id
            or purchase.offering_id != preview.offering_id
            or purchase.input_hash != preview.input_hash
        ):
            raise ValueError('purchase identity or input hash mismatch')
        if (
            purchase.execution_mode != 'clink_allowance'
            or preview.execution_mode != 'clink_allowance'
            or purchase.state not in {'paid_but_undelivered', 'payment_submitted'}
        ):
            raise ValueError('purchase is not eligible for paid recovery')
        if not purchase.reservation_id:
            raise ValueError('verified payment reservation is required')
        reservation = self.core.reservation(purchase.reservation_id)
        if not self._core_scope_matches(purchase, preview, reservation):
            raise ValueError('Core reservation scope mismatch')
        settlement = self.core.reconcile(purchase.reservation_id)
        if not self._core_scope_matches(purchase, preview, settlement):
            raise ValueError('Core settlement scope mismatch')
        self.purchases.restore_paid_input(
            purchase,
            preview,
            {'csv_text': csv_text},
            settlement,
        )
        return self.execute(purchase.preview_id)

    def _register_catalog(self):
        provider, offerings = super()._register_catalog()
        provider.metadata['settlement_mode'] = 'monad_testnet'
        self.repository.upsert_provider(provider)
        for offering in offerings:
            offering.metadata['settlement_mode'] = 'monad_testnet'
            self.repository.upsert_offering(offering)
        return provider, offerings
