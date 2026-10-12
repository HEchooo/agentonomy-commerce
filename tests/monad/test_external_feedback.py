"""External Agent feedback is routed by device, then signed by its own buyer."""
from copy import deepcopy
import json
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from examples.monad_commerce.external_api import create_external_app
from examples.monad_commerce.external_opc import ExternalHostedCommerceService
from examples.monad_commerce.hosted_canary import HostedCanary
from examples.monad_commerce.hosted_api import SESSION_COOKIE, CSRF_COOKIE
from examples.monad_commerce.public_worker import validate_bootstrap
from test_external_opc import DeviceCanary, NOW, active, login, proof
from test_hosted_erc8004 import Registry, delivered, FEEDBACK, OTHER
from test_hosted_service import configuration, ORIGIN, OWNER_A, OWNER_B
from shared.hosted_facilitator_protocol import DeviceSigningKey


class FeedbackCanary(DeviceCanary):
    orders_by_owner = {}
    auth_by_owner = {}
    _verified_gate_proof = HostedCanary._verified_gate_proof

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.network = validate_bootstrap(self.config).network
        self.tenant_id = kwargs['tenant_id']
        self.orders = self.orders_by_owner.setdefault(self.owner, {})
        self.authorized = self.auth_by_owner.setdefault(self.owner, set())

    def request(self, method, args):
        self.calls.append((method, deepcopy(args)))
        if method == 'purchase':
            item = self.orders[args['purchase_id']]
            if args.get('opc_installation_id') != item['device']:
                raise PermissionError('other device order')
            return deepcopy(item['purchase'])
        raise AssertionError(method)


@pytest.fixture
def prepared(tmp_path):
    NOW[0] = 2000000000
    DeviceCanary.devices, DeviceCanary.tokens = {}, {}
    FeedbackCanary.orders_by_owner, FeedbackCanary.auth_by_owner = {}, {}
    registry = Registry()
    with ExternalHostedCommerceService(tmp_path, configuration(), public_origin=ORIGIN,
            registry=registry, canary_factory=FeedbackCanary, clock=lambda: NOW[0]) as service:
        key = DeviceSigningKey.generate()
        browser, csrf, _ = active(service, key)
        issued = service.device_request('token', proof(key, 'token'))
        issued['test_device_key'] = key
        _, canary = service._credential(issued['access_token'])
        canary.orders['purchase_a'] = {'device': issued['installation_id'], 'purchase': delivered()}
        yield service, registry, issued, browser, csrf


def draft(prepared):
    service, _, issued, _, _ = prepared
    return service.device_operation(issued['access_token'], 'feedback_prepare',
                                    {'purchase_id': 'purchase_a', 'score': 87})


def intent(value):
    parsed = urlsplit(value['verification_uri'])
    assert parsed.scheme + '://' + parsed.netloc == ORIGIN
    assert parsed.path.startswith('/feedback/')
    return parsed.path.rsplit('/', 1)[1]


def test_agent_feedback_requires_own_delivered_order_and_returns_no_transaction(prepared):
    service, _, issued, _, _ = prepared
    value = draft(prepared)
    assert value['status'] == 'prepared' and value['score'] == 87
    assert 'transaction' not in value and 'disclosure' not in value
    assert len(intent(value)) == 43
    assert draft(prepared) == value
    assert 'NEVER-PUBLISH-CSV' not in json.dumps(value)
    _, canary = service._credential(issued['access_token'])
    assert canary.calls[-1][1]['opc_installation_id'] == issued['installation_id']
    canary.orders['purchase_a']['device'] = 'other-device'
    for operation, args in [('feedback_prepare', {'score': 87}), ('feedback_status', {}),
                            ('feedback_verify', {'transaction_hash': FEEDBACK})]:
        with pytest.raises(PermissionError):
            service.device_operation(issued['access_token'], operation, {'purchase_id': 'purchase_a'} | args)


def test_feedback_page_is_owner_authenticated_and_csrf_scoped(prepared):
    service, _, _, browser, csrf = prepared
    link = intent(draft(prepared))
    with pytest.raises(PermissionError):
        service.feedback_consent('z'*43, link)
    other, other_csrf = login(service, OWNER_B)
    with pytest.raises(PermissionError):
        service.feedback_consent(other, link)
    with pytest.raises(PermissionError):
        service.verify_feedback_consent(browser, other_csrf, link, FEEDBACK)
    view = service.feedback_consent(browser, link)
    assert view['owner'] == OWNER_A and view['chain_id'] == 10143
    assert view['transaction']['from'] == OWNER_A
    assert view['reputation_registry'] == service.registry.config.reputation_registry
    assert view['disclosure']['payment_transaction_hash'] == delivered()['settlement']['transaction_hash']
    assert service.feedback_consent(browser, link) == view


def test_pending_then_verified_roundtrip_returns_evidence_to_agent(prepared):
    service, registry, issued, browser, csrf = prepared
    value = draft(prepared)
    link = intent(value)
    registry.proof = None
    pending = service.verify_feedback_consent(browser, csrf, link, FEEDBACK)
    assert pending['status'] == 'pending' and 'transaction' not in pending
    with pytest.raises(ValueError):
        service.verify_feedback_consent(browser, csrf, link, OTHER)
    registry.proof = {'verified': True, 'two_rpc_verified': True, 'transaction_hash': FEEDBACK,
                      'feedback_index': 1, 'is_revoked': False, 'chain_id': 10143,
                      'agent_id': 0, 'agent_registry': 'eip155:10143:0x'+'99'*20, 'score': 87}
    result = service.device_operation(issued['access_token'], 'feedback_status', {'purchase_id': 'purchase_a'})
    assert result['verified'] is True and result['transaction_hash'] == FEEDBACK
    assert 'transaction' not in result and 'verification_uri' not in result
    assert service.public_feedback(value['feedback_hash'])['value'] == 87
    profile = service._service_history('csv-reconciliation-v1')
    assert profile['buyer_feedback']['sample_size'] == 1
    assert profile['buyer_feedback']['average_score'] == '87'
    assert profile['freshness']['feedback_last_verified_at'] is not None
    assert 'transaction' not in service.feedback_consent(browser, link)


def test_expired_link_is_not_authority_and_new_intent_never_resends_pending_feedback(prepared):
    service, _, issued, browser, csrf = prepared
    value = draft(prepared)
    link = intent(value)
    service.verify_feedback_consent(browser, csrf, link, FEEDBACK)
    NOW[0] += 601
    with pytest.raises(PermissionError):
        service.feedback_consent(browser, link)
    # Refreshing a consent link needs a fresh device credential, not a new order.
    issued.update(service.device_request('token', proof(issued['test_device_key'], 'token', 'fresh-token-request')))
    value2 = draft(prepared)
    assert value2['status'] == 'pending' and 'transaction' not in value2


def test_feedback_does_not_accept_agent_supplied_tenant_or_signature(prepared):
    service, _, issued, _, _ = prepared
    for extra in ({'owner': OWNER_B}, {'opc_installation_id': 'fake'}, {'signature': 'secret'}):
        with pytest.raises(ValueError):
            service.device_operation(issued['access_token'], 'feedback_prepare',
                                     {'purchase_id': 'purchase_a', 'score': 87} | extra)


def test_feedback_http_owner_origin_and_csrf_boundaries(prepared):
    service, _, _, browser, csrf = prepared
    link = intent(draft(prepared))
    class Entered:
        def __enter__(self): return service
        def __exit__(self, *_): pass
    app = create_external_app(origin=ORIGIN, runtime_factory=Entered)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get('/feedback/' + link).status_code == 200
        assert client.get('/api/feedback/' + link).status_code == 401
        client.cookies.set(SESSION_COOKIE, browser)
        client.cookies.set(CSRF_COOKIE, csrf)
        response = client.get('/api/feedback/' + link)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        target = '/api/feedback/' + link + '/verify'
        assert client.post(target, json={'transaction_hash': FEEDBACK}, headers={'Origin': ORIGIN}).status_code == 403
        headers = {'Origin': ORIGIN, 'x-agentonomy-csrf': csrf}
        assert client.post(target, json={'transaction_hash': FEEDBACK}, headers=headers).status_code == 200
        response = client.post(target, json={'transaction_hash': FEEDBACK, 'signature': 'private-marker'}, headers=headers)
        assert response.status_code == 422 and 'private-marker' not in response.text
        assert client.post(target, json={'transaction_hash': FEEDBACK},
                           headers=headers | {'Origin': 'https://wrong.example'}).status_code == 403


def test_feedback_intent_and_original_transaction_survive_restart(prepared):
    service, registry, issued, browser, csrf = prepared
    value = draft(prepared)
    linked = intent(value)
    service.verify_feedback_consent(browser, csrf, linked, FEEDBACK)
    path = service.state_dir
    service.__exit__(None, None, None)
    with ExternalHostedCommerceService(path, configuration(), public_origin=ORIGIN,
            registry=registry, canary_factory=FeedbackCanary, clock=lambda: NOW[0]) as restored:
        pending = restored.feedback_consent(browser, linked)
        assert pending['transaction_hash'] == FEEDBACK and 'transaction' not in pending
        with pytest.raises(ValueError):
            restored.verify_feedback_consent(browser, csrf, linked, OTHER)
        registry.proof = {'verified': True, 'two_rpc_verified': True, 'transaction_hash': FEEDBACK,
                          'feedback_index': 1, 'is_revoked': False, 'chain_id': 10143,
                      'agent_id': 0, 'agent_registry': 'eip155:10143:0x'+'99'*20, 'score': 87}
        accepted = restored.device_operation(issued['access_token'], 'feedback_status', {'purchase_id': 'purchase_a'})
        assert accepted['verified'] is True
        assert restored._service_history('csv-reconciliation-v1')['observed_deliveries'] == 1


def test_shared_history_counts_replay_once_and_keeps_private_orders_isolated(prepared):
    service, registry, issued, _, _ = prepared
    draft(prepared)
    first = service.device_operation(issued['access_token'], 'purchase', {'purchase_id': 'purchase_a'})
    repeated = service.device_operation(issued['access_token'], 'purchase', {'purchase_id': 'purchase_a'})
    assert first['settlement'] == repeated['settlement']
    profile = service._service_history('csv-reconciliation-v1')
    assert profile['observed_deliveries'] == 1
    assert profile['buyer_feedback']['sample_size'] == 0
    other = DeviceSigningKey.generate()
    active(service, other, OWNER_B)
    token = service.device_request('token', proof(other, 'token'))['access_token']
    viewed = service.device_operation(token, 'service_history', {'offering_id': 'csv-reconciliation-v1'})
    assert viewed == profile
    text = json.dumps(viewed)
    for private in ('purchase_a', 'preview_a', 'NEVER-PUBLISH-CSV', 'PRIVATE-INPUT-HASH', OWNER_A, issued['installation_id']):
        assert private not in text
    with pytest.raises(KeyError):
        service.device_operation(token, 'purchase', {'purchase_id': 'purchase_a'})


def test_catalog_reads_include_shared_history_without_turning_operational_score_into_rating(prepared):
    service, _, issued, _, _ = prepared
    draft(prepared)
    _, canary = service._credential(issued['access_token'])
    original = canary.request
    def catalog(method, args):
        if method == 'search':
            return {'count': 1, 'items': [{'offering_id': 'csv-reconciliation-v1'}]}
        if method == 'details':
            return {'offering': {'offering_id': 'csv-reconciliation-v1'},
                    'reputation': {'score': 50, 'sample_size': 0, 'confidence': 0}}
        return original(method, args)
    canary.request = catalog
    result = service.device_operation(issued['access_token'], 'search', {})
    assert result['items'][0]['service_history']['observed_deliveries'] == 1
    result = service.device_operation(issued['access_token'], 'details', {'offering_id': 'csv-reconciliation-v1'})
    assert result['service_history']['buyer_feedback']['average_score'] is None
    assert result['reputation']['score'] == 50


def test_history_projection_failure_never_hides_a_paid_result_or_reexecutes(prepared, monkeypatch):
    service, _, issued, _, _ = prepared
    monkeypatch.setattr(service.history, 'record_purchase', lambda **_: (_ for _ in ()).throw(ValueError('identity unavailable')))
    result = service.device_operation(issued['access_token'], 'purchase', {'purchase_id': 'purchase_a'})
    assert result['state'] == 'delivered' and result['settlement']['verified'] is True
    assert result['service_history_update']['status'] == 'deferred'
    _, canary = service._credential(issued['access_token'])
    assert all(method != 'execute' for method, _ in canary.calls)


def test_renewal_http_requires_wallet_csrf_and_exact_fields(prepared):
    service, _, _, browser, csrf = prepared
    _, canary = service._authorize(browser, csrf)
    calls = []
    def onboarding(operation, **params):
        calls.append((operation, params))
        return {'status': 'verified'}
    canary.onboarding = onboarding
    class Entered:
        def __enter__(self): return service
        def __exit__(self, *_): pass
    app = create_external_app(origin=ORIGIN, runtime_factory=Entered)
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set(SESSION_COOKIE, browser)
        client.cookies.set(CSRF_COOKIE, csrf)
        path = '/api/grant/renew/challenge'
        headers = {'Origin': ORIGIN, 'x-agentonomy-csrf': csrf}
        assert client.post(path, json={}, headers={'Origin': ORIGIN}).status_code == 403
        assert client.post(path, json={'owner': OWNER_B}, headers=headers).status_code == 422
        assert client.post(path, json={}, headers=headers).status_code == 200
        assert calls[-1][0] == 'grant_renew_challenge'
        assert set(calls[-1][1]) == {'browser_digest', 'csrf_digest'}
        signature = '0x' + '12' * 65
        assert client.post('/api/grant/renew/verify', json={'signature': signature}, headers=headers).status_code == 200
        assert calls[-1] == ('grant_renew_verify', {'signature': signature})
    with pytest.raises(ValueError):
        service.dispatch(browser, csrf, 'grant_renew_verify', {'signature': signature, 'grant_id': 'other'})


def test_stored_verified_feedback_does_not_claim_fresh_observation_when_rpc_pending(prepared):
    service, registry, issued, browser, csrf = prepared
    link = intent(draft(prepared))
    registry.proof = {'verified': True, 'two_rpc_verified': True, 'transaction_hash': FEEDBACK,
                      'feedback_index': 1, 'is_revoked': False, 'chain_id': 10143,
                      'agent_id': 0, 'agent_registry': 'eip155:10143:0x'+'99'*20, 'score': 87}
    first = service.verify_feedback_consent(browser, csrf, link, FEEDBACK)
    assert first['verification_status'] == 'current'
    observed = first['last_verified_at']
    registry.proof = None
    result = service.device_operation(issued['access_token'], 'feedback_status', {'purchase_id': 'purchase_a'})
    assert result['verified'] is True  # Explicitly historical, not a new proof.
    assert result['last_verified_at'] == observed
    assert result['verification_status'] == 'unconfirmed'
