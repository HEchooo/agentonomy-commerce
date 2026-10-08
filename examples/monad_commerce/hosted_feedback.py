"""Immutable buyer feedback drafts; public only after independent chain proof.

Uses the hosted registry's existing private database. No keys, signatures,
input CSV, reports or credential material enter a feedback document.
"""
from datetime import UTC, datetime
import json
import re

from eth_utils import keccak

from agentonomy_commerce.budget_network import address
from agentonomy_commerce.wallet_transactions import transaction_hash


_DIGEST = re.compile(r'^0x[0-9a-f]{64}$')


def canonical_document(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


class FeedbackStore:
    def __init__(self, database, registry, *, clock):
        self.db, self.registry, self.clock = database, registry, clock
        self.db.execute('''CREATE TABLE IF NOT EXISTS erc8004_feedback (
            tenant_id TEXT NOT NULL, purchase_id TEXT NOT NULL, buyer TEXT NOT NULL,
            score INTEGER NOT NULL, document TEXT NOT NULL, feedback_hash TEXT UNIQUE NOT NULL,
            transaction_payload TEXT NOT NULL, transaction_hash TEXT,
            status TEXT NOT NULL, evidence TEXT,
            PRIMARY KEY(tenant_id, purchase_id))''')
        self.db.commit()

    def _row(self, tenant_id, purchase_id, buyer):
        row = self.db.execute('SELECT * FROM erc8004_feedback WHERE tenant_id=? AND purchase_id=?',
                              (tenant_id, purchase_id)).fetchone()
        if row is not None and row['buyer'] != address(buyer):
            raise ValueError('feedback belongs to another wallet')
        return row

    def _view(self, row, *, transaction=False):
        result = {'status': row['status'], 'score': row['score'],
                  'feedback_hash': row['feedback_hash'],
                  'feedback_uri': self.registry.origin + '/erc8004/feedback/' + row['feedback_hash'][2:] + '.json',
                  'transaction_hash': row['transaction_hash'],
                  'verified': row['status'] == 'verified'}
        if row['evidence']:
            proof = json.loads(row['evidence'])
            result.update(feedback_index=proof['feedback_index'], is_revoked=proof['is_revoked'])
        if transaction and row['status'] == 'prepared':
            result['transaction'] = json.loads(row['transaction_payload'])
            doc = json.loads(row['document'])
            result['disclosure'] = {'buyer': row['buyer'], 'score': row['score'],
                                    'payment_transaction_hash': doc['proofOfPayment']['txHash'],
                                    'output_hash': doc['delivery']['outputHash']}
        return result

    def prepare(self, *, tenant_id, purchase_id, buyer, score, purchase, identity):
        buyer = address(buyer)
        if type(score) is not int or not 0 <= score <= 100:
            raise ValueError('feedback score must be an integer0..100')
        if (purchase.get('purchase_id') != purchase_id or purchase.get('state') != 'delivered'
            or purchase.get('offering_id') != 'csv-reconciliation-v1'
            or type(purchase.get('output_hash')) is not str
            or not _DIGEST.fullmatch(purchase['output_hash'])
            or identity.get('verified') is not True):
            raise ValueError('verified delivered CSV order required')
        row = self._row(tenant_id, purchase_id, buyer)
        if row is not None:
            if row['score'] != score:
                raise ValueError('original feedback score is already frozen')
            return self._view(row, transaction=True)
        payment = purchase['settlement']
        if payment.get('owner') != buyer:
            raise ValueError('payment belongs to another buyer')
        doc = {'agentRegistry': identity['agent_registry'], 'agentId': identity['agent_id'],
               'clientAddress': f'eip155:{self.registry.network.chain_id}:{buyer}',
               'createdAt': datetime.fromtimestamp(self.clock(), UTC).isoformat().replace('+00:00', 'Z'),
               'value': score, 'valueDecimals': 0, 'tag1': 'starred', 'tag2': 'csv-reconciliation',
               'endpoint': self.registry.origin + '/',
               'proofOfPayment': {'fromAddress': buyer, 'toAddress': payment['payee'],
                                  'chainId': str(self.registry.network.chain_id),
                                  'txHash': transaction_hash(payment['transaction_hash'])},
               'delivery': {'outputHash': 'sha256:' + purchase['output_hash'][2:]}}
        raw = canonical_document(doc)
        digest = '0x' + keccak(raw.encode('utf-8')).hex()
        uri = self.registry.origin + '/erc8004/feedback/' + digest[2:] + '.json'
        transaction = self.registry.feedback_transaction(buyer=buyer, score=score,
                              feedback_uri=uri, feedback_hash=digest)
        self.db.execute('INSERT INTO erc8004_feedback VALUES(?,?,?,?,?,?,?,NULL,?,NULL)',
                        (tenant_id, purchase_id, buyer, score, raw, digest,
                         canonical_document(transaction), 'prepared'))
        self.db.commit()
        return self._view(self._row(tenant_id, purchase_id, buyer), transaction=True)

    def verify(self, *, tenant_id, purchase_id, buyer, tx_hash):
        tx_hash = transaction_hash(tx_hash)
        row = self._row(tenant_id, purchase_id, buyer)
        if row is None:
            raise ValueError('original feedback draft required')
        if row['transaction_hash'] and row['transaction_hash'] != tx_hash:
            raise ValueError('verify the original feedback transaction')
        if not row['transaction_hash']:
            # Commit the candidate BEFORE network I/O, including timeout/failure.
            self.db.execute('UPDATE erc8004_feedback SET transaction_hash=?,status=? WHERE tenant_id=? AND purchase_id=?',
                            (tx_hash, 'pending', tenant_id, purchase_id))
            self.db.commit()
        proof = self.registry.verify_feedback(buyer=buyer, tx_hash=tx_hash,
                              transaction=json.loads(row['transaction_payload']))
        if proof is not None:
            if (proof.get('verified') is not True or proof.get('two_rpc_verified') is not True
                or proof.get('transaction_hash') != tx_hash
                or type(proof.get('feedback_index')) is not int or proof['feedback_index'] < 1
                or type(proof.get('is_revoked')) is not bool):
                raise ValueError('independent feedback evidence required')
            self.db.execute('UPDATE erc8004_feedback SET status=?,evidence=? WHERE tenant_id=? AND purchase_id=?',
                            ('verified', canonical_document(proof), tenant_id, purchase_id))
            self.db.commit()
        return self.status(tenant_id=tenant_id, purchase_id=purchase_id, buyer=buyer)

    def status(self, *, tenant_id, purchase_id, buyer):
        row = self._row(tenant_id, purchase_id, buyer)
        return {'status': 'unprepared', 'verified': False} if row is None else self._view(row)

    def public(self, digest):
        if type(digest) is not str or not _DIGEST.fullmatch(digest):
            raise ValueError('feedback content hash required')
        row = self.db.execute('SELECT document FROM erc8004_feedback WHERE feedback_hash=? AND status=?',
                              (digest, 'verified')).fetchone()
        return None if row is None else json.loads(row['document'])
