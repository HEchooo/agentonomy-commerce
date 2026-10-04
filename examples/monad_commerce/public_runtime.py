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

    def recover_purchase(self, purchase_id):
        purchase = self.repository.get_purchase(purchase_id)
        if purchase is None:
            raise KeyError('purchase not found')
        return self.execute(purchase.preview_id)

    def _register_catalog(self):
        provider, offerings = super()._register_catalog()
        provider.metadata['settlement_mode'] = 'monad_testnet'
        self.repository.upsert_provider(provider)
        for offering in offerings:
            offering.metadata['settlement_mode'] = 'monad_testnet'
            self.repository.upsert_offering(offering)
        return provider, offerings
