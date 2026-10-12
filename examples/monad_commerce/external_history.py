"""Shared public evidence projection alongside wallet-private commerce state."""
import sqlite3

from examples.monad_commerce.hosted_history import MerchantHistoryStore
from examples.monad_commerce.rehearsal import public_purchase


OFFERING_ID = 'csv-reconciliation-v1'


class ExternalHistoryMixin:
    def _init_service_history(self):
        self.history = MerchantHistoryStore(self.db, clock=self.clock)

    def _service_history(self, offering_id):
        if offering_id != OFFERING_ID:
            raise ValueError('unknown service identity')
        if self.registry is None:
            return {'status': 'identity_unavailable', 'observed_deliveries': 0,
                    'buyer_feedback': {'sample_size': 0, 'average_score': None}}
        return self.history.profile(identity=self._service_identity(), feedback=self.feedback)

    def _observe_purchase(self, row, canary, purchase):
        result = public_purchase(purchase)
        if purchase.get('state') != 'delivered' or self.registry is None:
            return result
        try:
            if canary._verified_gate_proof(purchase, purchase['purchase_id']) is None:
                raise ValueError('verified payment required for public history')
            self.history.record_purchase(tenant_id=row['tenant_id'], owner=row['owner'],
                    purchase=purchase, identity=self._service_identity(), network=self.scope.network)
        except (ValueError, RuntimeError, sqlite3.Error):
            # History indexing is not settlement. A recoverable projection
            # failure must never hide the paid order or cause another charge.
            return result | {'service_history_update': {'status': 'deferred',
                                                       'retry': 'query_original_purchase'}}
        return result | {'service_history_update': {'status': 'recorded'}}

    def _catalog_history(self, operation, value):
        if self.registry is None:
            return value
        profile = self._service_history(OFFERING_ID)
        if operation == 'search':
            return value | {'items': [item | {'service_history': profile}
                    if item.get('offering_id') == OFFERING_ID else item
                    for item in value.get('items', [])]}
        return value | {'service_history': profile}
