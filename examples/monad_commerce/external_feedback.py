"""Device-scoped feedback intent; only the authenticated buyer signs on the web.

A link routes an immutable draft. It is neither a login nor spending authority.
The browser API never accepts a score, recipient, purchase ID or signature.
"""
import base64
import hashlib
import hmac
import re

from examples.monad_commerce.hosted_service import _digest

_PURCHASE = re.compile(r'^purchase_[A-Za-z0-9][A-Za-z0-9_-]{0,159}$')
_FIELDS = {
    'feedback_prepare': {'purchase_id', 'score'},
    'feedback_status': {'purchase_id'},
    'feedback_verify': {'purchase_id', 'transaction_hash'},
}


class ExternalFeedbackMixin:
    def _init_feedback_intents(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS external_feedback_intents (
            token_digest TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, owner TEXT NOT NULL,
            installation_id TEXT NOT NULL, purchase_id TEXT NOT NULL,
            feedback_hash TEXT NOT NULL, expires INTEGER NOT NULL)''')
        self.db.commit()

    def _feedback_purchase(self, row, canary, purchase_id):
        value = canary.request('purchase', {'purchase_id': purchase_id,
                              'opc_installation_id': row['installation_id']})
        if canary._verified_gate_proof(value, purchase_id) is None:
            raise ValueError('Core-verified original payment required')
        return value

    def _feedback_link(self, row, purchase_id, value):
        now = int(self.clock())
        expires = (now // 600 + 1) * 600
        message = '\0'.join(('feedback-v1', row['installation_id'], row['tenant_id'],
                             purchase_id, value['feedback_hash'], str(expires)))
        raw = hmac.new(self._routing_secret, message.encode(), hashlib.sha256).digest()
        link = base64.urlsafe_b64encode(raw).decode().rstrip('=')
        self.db.execute('DELETE FROM external_feedback_intents WHERE expires<=?', (now,))
        if self.db.execute('SELECT COUNT(*) FROM external_feedback_intents').fetchone()[0] >= 512:
            if not self.db.execute('SELECT 1 FROM external_feedback_intents WHERE token_digest=?',
                                   (_digest(link),)).fetchone():
                raise ValueError('feedback consent admission full')
        self.db.execute('INSERT OR IGNORE INTO external_feedback_intents VALUES(?,?,?,?,?,?,?)',
                        (_digest(link), row['tenant_id'], row['owner'], row['installation_id'],
                         purchase_id, value['feedback_hash'], expires))
        self.db.commit()
        return {'verification_uri': self.public_origin + '/feedback/' + link,
                'consent_expires_at': expires}

    def _device_feedback(self, row, canary, operation, args):
        if operation not in _FIELDS or type(args) is not dict or set(args) != _FIELDS[operation]:
            raise ValueError('feedback fields are outside scope')
        if self.feedback is None:
            raise ValueError('ERC-8004 feedback not configured')
        purchase_id = args['purchase_id']
        if type(purchase_id) is not str or not _PURCHASE.fullmatch(purchase_id):
            raise ValueError('canonical purchase ID required')
        # Even status/verification must belong to this installation, not just
        # another device logged in to the same wallet.
        purchase = self._feedback_purchase(row, canary, purchase_id)
        self._observe_purchase(row, canary, purchase)
        scope = dict(tenant_id=row['tenant_id'], purchase_id=purchase_id, buyer=row['owner'])
        if operation == 'feedback_prepare':
            value = self.feedback.prepare(**scope, score=args['score'], purchase=purchase,
                                          identity=self._service_identity())
        else:
            value = self.feedback.status(**scope)
            tx_hash = args.get('transaction_hash') or value.get('transaction_hash')
            if tx_hash:
                # Re-observe the original transaction and current revocation
                # state. An unavailable observer cannot turn stale evidence
                # into a new positive verification.
                value = self.feedback.verify(**scope, tx_hash=tx_hash)
        public = {key: item for key, item in value.items() if key not in {'transaction', 'disclosure'}}
        if operation == 'feedback_prepare' and not value['verified']:
            public |= self._feedback_link(row, purchase_id, value)
        return public

    def _feedback_intent(self, browser_token, intent, csrf=None, *, mutation=False):
        row, canary = self._authorize(browser_token, csrf, mutation=mutation)
        value = self.db.execute('SELECT * FROM external_feedback_intents WHERE token_digest=?',
                                (_digest(intent),)).fetchone()
        if (value is None or value['expires'] <= int(self.clock())
            or value['tenant_id'] != row['tenant_id'] or value['owner'] != row['owner']):
            raise PermissionError('feedback consent unavailable')
        return row, canary, dict(value)

    def _feedback_consent_view(self, row, canary, intent):
        scope = dict(tenant_id=row['tenant_id'], purchase_id=intent['purchase_id'], buyer=row['owner'])
        value = self.feedback.status(**scope)
        if value.get('feedback_hash') != intent['feedback_hash']:
            raise PermissionError('feedback consent mismatch')
        if value['status'] == 'prepared':
            purchase = self._feedback_purchase(intent, canary, intent['purchase_id'])
            value = self.feedback.prepare(**scope, score=value['score'], purchase=purchase,
                                          identity=self._service_identity())
        network = self.scope.network
        return value | {'owner': row['owner'], 'chain_id': network.chain_id,
                        'reputation_registry': self.registry.config.reputation_registry,
                        'agent_id': self.registry.config.agent_id,
                        'agent_registry': self._service_identity()['agent_registry'],
                        'wallet_transaction': {'max_gas': str(network.gas_limit),
                                               'max_gas_price_wei': str(network.max_gas_price_wei)}}

    def feedback_consent(self, browser_token, intent):
        with self._lock:
            row, canary, linked = self._feedback_intent(browser_token, intent)
            return self._feedback_consent_view(row, canary, linked)

    def verify_feedback_consent(self, browser_token, csrf_token, intent, tx_hash):
        with self._lock:
            row, canary, linked = self._feedback_intent(browser_token, intent, csrf_token, mutation=True)
            # The buyer verifies exactly the frozen draft; the transaction hash
            # is committed before RPC I/O inside FeedbackStore.verify.
            result = self.feedback.verify(tenant_id=row['tenant_id'], purchase_id=linked['purchase_id'],
                                          buyer=row['owner'], tx_hash=tx_hash)
            return self._feedback_consent_view(row, canary, linked) | {
                'verification_status': result['verification_status']}
