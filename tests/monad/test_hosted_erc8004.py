"""Hosted identity gating and buyer-authorized publication, without real AWS."""
import hashlib
import json
from types import SimpleNamespace

from eth_utils import keccak
from fastapi.testclient import TestClient
import pytest

from examples.monad_commerce.hosted_api import create_app
from examples.monad_commerce.hosted_canary import HostedCanary
from examples.monad_commerce.hosted_service import HostedCommerceService
from examples.monad_commerce.public_worker import validate_bootstrap
from test_hosted_service import (
    configuration, PersistentCanary, login, OWNER_A, OWNER_B, ORIGIN,
)

PAYMENT = '0x' + '12' * 32
FEEDBACK = '0x' + '34' * 32
OTHER = '0x' + '56' * 32


class Registry:
    def __init__(self):
        self.network = validate_bootstrap(configuration()).network
        self.origin = ORIGIN
        self.config = SimpleNamespace(agent_id=0, agent_owner='0x'+'77'*20,
                                      reputation_registry='0x'+'88'*20)
        self.failed = False
        self.proof = None
        self.verifications = 0

    def verify_identity(self):
        self.verifications += 1
        if self.failed:
            raise ValueError('registry changed')
        return {'verified': True, 'agent_registry': 'eip155:10143:0x'+'99'*20,
                'agent_id': 0, 'agent_owner': self.config.agent_owner,
                'agent_wallet': self.network.payee, 'agent_uri': ORIGIN+'/agent.json',
                'chain_id': 10143, 'block_number': 100, 'block_hash': PAYMENT}

    def registration_document(self):
        identity = self.verify_identity()
        return {'type': 'https://eips.ethereum.org/EIPS/eip-8004#registration-v1',
                'name': 'Agentonomy CSV Reconciliation', 'description': 'CSV service',
                'image': ORIGIN+'/agent.svg', 'services': [{'name': 'web', 'endpoint': ORIGIN+'/'}],
                'active': True, 'x402Support': False, 'supportedTrust': ['reputation'],
                'registrations': [{'agentId': 0, 'agentRegistry': identity['agent_registry']}]}

    def feedback_transaction(self, *, buyer, score, feedback_uri, feedback_hash):
        self.verify_identity()
        return {'from': buyer, 'to': self.config.reputation_registry, 'chainId': '0x279f',
                'value': '0x0', 'gas': '0x7a120', 'data': '0x'+keccak(
                    text=json.dumps([buyer, score, feedback_uri, feedback_hash])).hex()}

    def verify_feedback(self, *, buyer, tx_hash, transaction):
        return self.proof


class OrderCanary(PersistentCanary):
    _verified_gate_proof = HostedCanary._verified_gate_proof

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.network = validate_bootstrap(self.config).network
        self.payment = {'pay_to': self.network.payee, 'network': 'eip155:10143',
                        'asset': self.network.token, 'amount_atomic': '300000'}
        self.market = SimpleNamespace(request=lambda method, args: {
            'preview_id': args['preview_id'], 'payment': self.payment,
        })

    def _start_market(self):
        pass


def delivered(owner=OWNER_A):
    report = {'private_input': 'NEVER-PUBLISH-CSV', 'net': '8.00'}
    output_hash = '0x' + hashlib.sha256(json.dumps(report, sort_keys=True,
                                separators=(',', ':')).encode()).hexdigest()
    return {'purchase_id': 'purchase_a', 'preview_id': 'preview_a',
            'offering_id': 'csv-reconciliation-v1', 'state': 'delivered',
            'input_hash': 'PRIVATE-INPUT-HASH', 'output_hash': output_hash,
            'service_result': report, 'settlement': {
                'verified': True, 'status': 'verified', 'two_rpc_verified': True,
                'chain_id': 10143, 'receipt_status': 1, 'transaction_hash': PAYMENT,
                'token': '0x'+'11'*20, 'payee': '0x'+'33'*20,
                'executor': '0x'+'22'*20, 'owner': owner, 'amount_atomic': '300000',
                'purchase_commitment': PAYMENT, 'quote_commitment': PAYMENT,
                'grant_hash': PAYMENT, 'block_hash': PAYMENT, 'finality_block_hash': PAYMENT,
            }}


@pytest.fixture
def service(tmp_path):
    OrderCanary.created = []
    OrderCanary.orders_by_tenant = {}
    OrderCanary.authorized_by_tenant = {}
    registry = Registry()
    with HostedCommerceService(tmp_path, configuration(), public_origin=ORIGIN,
                               canary_factory=OrderCanary, clock=lambda: 1000,
                               registry=registry) as value:
        login(value, 'x'*43, 'y'*43, OWNER_A)
        OrderCanary.created[-1].orders['purchase_a'] = delivered()
        yield value, registry


def test_public_registration_routes_without_browser_session():
    class Public:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def registration_document(self): return Registry().registration_document()
    with TestClient(create_app(origin=ORIGIN, runtime_factory=Public), base_url=ORIGIN) as client:
        for path in ('/agent.json', '/.well-known/agent-registration.json'):
            response = client.get(path)
            assert response.status_code == 200
            assert response.json()['registrations'][0]['agentId'] == 0
        assert client.get('/agent.svg').status_code == 200
        assert client.get('/api/agent').status_code == 401


def test_registration_pending_is_explicit_and_blocks_new_purchase(service, monkeypatch):
    value, registry = service
    monkeypatch.setattr(registry, 'verify_identity', lambda: None)
    assert value.dispatch('x'*43, 'y'*43, 'agent_identity', {}) == {
        'status': 'registration_pending', 'verified': False}
    with pytest.raises(ValueError, match='verified service receiver'):
        value.dispatch('x'*43, 'y'*43, 'preview', {'csv_text': 'x'})
    assert value.dispatch('x'*43, 'y'*43, 'recover_purchase', {'purchase_id': 'purchase_a'})['state'] == 'delivered'


def test_identity_changes_block_new_purchase_but_not_paid_recovery(service, monkeypatch):
    value, registry = service
    calls = []
    monkeypatch.setattr(value, '_agent_operation', lambda *args: calls.append(args) or {'ok': True})
    assert value.dispatch('x'*43, 'y'*43, 'preview', {'csv_text': 'x'}) == {'ok': True}
    registry.failed = True
    with pytest.raises(ValueError):
        value.dispatch('x'*43, 'y'*43, 'execute', {'preview_id': 'preview_a'})
    assert len(calls) == 1
    assert value.dispatch('x'*43, 'y'*43, 'recover_purchase', {'purchase_id': 'purchase_a'})['state'] == 'delivered'


def test_original_preview_payee_is_checked_before_execute(service, monkeypatch):
    value, _ = service
    OrderCanary.created[-1].payment['pay_to'] = OWNER_B
    monkeypatch.setattr(value, '_agent_operation', lambda *args: pytest.fail('must not execute'))
    with pytest.raises(ValueError):
        value.dispatch('x'*43, 'y'*43, 'execute', {'preview_id': 'preview_a'})


def test_feedback_is_frozen_private_until_chain_verified_and_no_raw_data(service):
    value, registry = service
    args = {'purchase_id': 'purchase_a', 'score': 87}
    result = value.dispatch('x'*43, 'y'*43, 'feedback_prepare', args)
    assert result['status'] == 'prepared' and result['transaction']['from'] == OWNER_A
    doc_hash = result['feedback_hash']
    assert value.public_feedback(doc_hash) is None
    assert value.dispatch('x'*43, 'y'*43, 'feedback_prepare', args) == result
    with pytest.raises(ValueError):
        value.dispatch('x'*43, 'y'*43, 'feedback_prepare', args | {'score': 100})
    pending = value.dispatch('x'*43, 'y'*43, 'feedback_verify',
                             {'purchase_id': 'purchase_a', 'transaction_hash': FEEDBACK})
    assert pending['status'] == 'pending' and pending['transaction_hash'] == FEEDBACK
    with pytest.raises(ValueError):
        value.dispatch('x'*43, 'y'*43, 'feedback_verify',
                       {'purchase_id': 'purchase_a', 'transaction_hash': OTHER})
    registry.proof = {'verified': True, 'two_rpc_verified': True, 'transaction_hash': FEEDBACK,
                      'feedback_index': 1, 'is_revoked': False}
    accepted = value.dispatch('x'*43, 'y'*43, 'feedback_verify',
                              {'purchase_id': 'purchase_a', 'transaction_hash': FEEDBACK})
    assert accepted['status'] == 'verified'
    public = value.public_feedback(doc_hash)
    assert public['proofOfPayment']['txHash'] == PAYMENT
    assert public['value'] == 87 and public['agentId'] == 0
    assert public['delivery']['outputHash'].startswith('sha256:')
    serialized = json.dumps(public)
    for secret in ('NEVER-PUBLISH-CSV', 'PRIVATE-INPUT-HASH', 'purchase_a', 'preview_a', 'tenant_', 'csrf'):
        assert secret not in serialized


@pytest.mark.parametrize('field,bad', [('state', 'paid_but_undelivered'),
                                     ('output_hash', 'wrong'), ('offering_id', 'other-service')])
def test_unverified_or_undelivered_order_cannot_prepare_feedback(service, field, bad):
    value, _ = service
    OrderCanary.created[-1].orders['purchase_a'][field] = bad
    with pytest.raises(ValueError):
        value.dispatch('x'*43, 'y'*43, 'feedback_prepare', {'purchase_id': 'purchase_a', 'score': 50})


def test_feedback_order_is_bound_to_current_owner_and_csrf(service):
    value, _ = service
    with pytest.raises(PermissionError):
        value.dispatch('x'*43, None, 'feedback_prepare', {'purchase_id': 'purchase_a', 'score': 90})
    login(value, 'q'*43, 'r'*43, OWNER_B)
    with pytest.raises((KeyError, ValueError)):
        value.dispatch('q'*43, 'r'*43, 'feedback_prepare', {'purchase_id': 'purchase_a', 'score': 90})
    OrderCanary.created[-1].orders['purchase_a'] = delivered(OWNER_A)
    with pytest.raises(ValueError):
        value.dispatch('q'*43, 'r'*43, 'feedback_prepare', {'purchase_id': 'purchase_a', 'score': 90})


def test_feedback_pending_candidate_survives_restart(service):
    value, _ = service
    draft = value.dispatch('x'*43, 'y'*43, 'feedback_prepare', {'purchase_id': 'purchase_a', 'score': 75})
    value.dispatch('x'*43, 'y'*43, 'feedback_verify', {'purchase_id': 'purchase_a', 'transaction_hash': FEEDBACK})
    path = value.state_dir
    value.__exit__(None, None, None)
    with HostedCommerceService(path, configuration(), public_origin=ORIGIN, registry=Registry(),
                               canary_factory=OrderCanary, clock=lambda: 1000) as restored:
        original = restored.dispatch('x'*43, None, 'feedback_status', {'purchase_id': 'purchase_a'})
        assert original['transaction_hash'] == FEEDBACK
        assert original['feedback_hash'] == draft['feedback_hash']
        assert original['status'] == 'pending'


def test_no_registry_configuration_has_explicit_unconfigured_identity(tmp_path):
    with HostedCommerceService(tmp_path, configuration(), public_origin=ORIGIN,
                               canary_factory=OrderCanary, clock=lambda: 1000) as value:
        login(value, 'x'*43, 'y'*43, OWNER_A)
        assert value.dispatch('x'*43, None, 'agent_identity', {}) == {'status': 'not_configured', 'verified': False}
        with pytest.raises(ValueError):
            value.dispatch('x'*43, 'y'*43, 'feedback_prepare', {'purchase_id': 'purchase_a', 'score': 90})


def test_feedback_http_routes_require_csrf_and_strict_score():
    from test_hosted_api import FakeHostedService
    api_service = FakeHostedService()
    with TestClient(create_app(origin=ORIGIN, runtime_factory=lambda: api_service), base_url=ORIGIN) as client:
        csrf = client.post('/api/session', json={}, headers={'Origin': ORIGIN}).json()['csrf_token']
        base = '/api/purchases/purchase_a/feedback'
        assert client.post(base+'/prepare', json={'score': 90}, headers={'Origin': ORIGIN}).status_code == 403
        headers = {'Origin': ORIGIN, 'X-Agentonomy-CSRF': csrf}
        for score in (True, '90', -1, 101):
            assert client.post(base+'/prepare', json={'score': score}, headers=headers).status_code == 422
        assert client.post(base+'/prepare', json={'score': 90, 'payee': OWNER_A}, headers=headers).status_code == 422
        result = client.post(base+'/prepare', json={'score': 90}, headers=headers)
        assert result.status_code == 200
        assert result.json()['args'] == {'purchase_id': 'purchase_a', 'score': 90}
        assert client.post(base+'/verify', json={'transaction_hash': FEEDBACK}, headers=headers).status_code == 200
        assert client.get(base).status_code == 200


def test_published_feedback_http_bytes_match_chain_commitment(service):
    value, registry = service
    draft = value.dispatch('x'*43, 'y'*43, 'feedback_prepare', {'purchase_id': 'purchase_a', 'score': 88})
    registry.proof = {'verified': True, 'two_rpc_verified': True, 'transaction_hash': FEEDBACK,
                      'feedback_index': 1, 'is_revoked': True}
    # Public app uses a context manager wrapper around the already-open state.
    class Public:
        def __enter__(self): return value
        def __exit__(self, *_): pass
    with TestClient(create_app(origin=ORIGIN, runtime_factory=Public), base_url=ORIGIN) as client:
        path = '/erc8004/feedback/'+draft['feedback_hash'][2:]+'.json'
        assert client.get(path).status_code == 404
        result = value.dispatch('x'*43, 'y'*43, 'feedback_verify',
                                {'purchase_id': 'purchase_a', 'transaction_hash': FEEDBACK})
        assert result['is_revoked'] is True
        response = client.get(path)
        assert response.status_code == 200
        assert '0x'+keccak(response.content).hex() == draft['feedback_hash']
        assert client.get('/erc8004/feedback/'+'00'*32+'.json').status_code == 404
