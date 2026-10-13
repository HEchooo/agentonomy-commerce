"""Shared public evidence projection alongside wallet-private commerce state."""
import re
import sqlite3

from examples.monad_commerce.hosted_assessment import AgentAssessmentStore
from examples.monad_commerce.hosted_history import MerchantHistoryStore
from examples.monad_commerce.rehearsal import public_purchase


OFFERING_ID = 'csv-reconciliation-v1'
_PURCHASE = re.compile(r'^purchase_[A-Za-z0-9][A-Za-z0-9_-]{0,159}$')
_ASSESSMENT_FIELDS = {
    'assessment_record': {'purchase_id', 'assessment'},
    'assessment_status': {'purchase_id'},
}


class ExternalHistoryMixin:
    def _init_service_history(self):
        self.history = MerchantHistoryStore(self.db, clock=self.clock)
        self.assessments = AgentAssessmentStore(self.db, clock=self.clock)

    def _service_history(self, offering_id):
        if offering_id != OFFERING_ID:
            raise ValueError('unknown service identity')
        if self.registry is None:
            return {'status': 'identity_unavailable', 'observed_deliveries': 0,
                    'buyer_feedback': {'sample_size': 0, 'average_score': None},
                    'agent_assessments': {'status': 'unassessed', 'sample_size': 0,
                                          'average_score': None}}
        identity = self._service_identity()
        profile = self.history.profile(identity=identity, feedback=self.feedback)
        return profile | {'agent_assessments': self.assessments.profile(identity=identity)}

    def _device_assessment(self, row, canary, operation, args):
        """Record or read one evidence-bound, off-chain Agent assessment.

        The assessment is a consumer Agent's bounded opinion, not a wallet
        action or a quality certification.  Reusing the existing purchase
        lookup and observation path keeps device, owner, Core proof, payment,
        and output-hash checks identical to the optional buyer-feedback flow.
        """
        fields = _ASSESSMENT_FIELDS.get(operation)
        if fields is None or type(args) is not dict or set(args) != fields:
            raise ValueError('assessment fields are outside scope')
        purchase_id = args['purchase_id']
        if type(purchase_id) is not str or _PURCHASE.fullmatch(purchase_id) is None:
            raise ValueError('canonical purchase ID required')

        # _feedback_purchase binds the read to this installation and requires
        # a current Core verified gate proof.  No payment/signing operation is
        # reachable from this branch.
        try:
            purchase = self._feedback_purchase(row, canary, purchase_id)
        except KeyError:
            # Do not expose whether another tenant or installation has an
            # order with this identifier.
            raise PermissionError('original purchase unavailable') from None
        if purchase.get('state') != 'delivered':
            raise ValueError('verified delivered purchase required')
        observed = self._observe_purchase(row, canary, purchase)
        if observed.get('service_history_update', {}).get('status') != 'recorded':
            raise ValueError('verified delivery history is required')

        scope = {
            'tenant_id': row['tenant_id'],
            'owner': row['owner'],
            'installation_id': row['installation_id'],
            'purchase': purchase,
            'identity': self._service_identity(),
            'network': self.scope.network,
        }
        if operation == 'assessment_record':
            return self.assessments.record(**scope, assessment=args['assessment'])
        return self.assessments.get(tenant_id=row['tenant_id'], owner=row['owner'],
                                    installation_id=row['installation_id'],
                                    purchase_id=purchase_id)

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
