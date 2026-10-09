"""Device routing is not authority: the tenant Core remains authoritative."""
from copy import deepcopy
import secrets

import pytest

from shared.hosted_facilitator_protocol import DeviceSigningKey
from shared.opc_protocol import sign_opc_proof, verify_opc_proof
from test_hosted_service import configuration, Canary, ORIGIN, OWNER_A, OWNER_B


class DeviceCanary(Canary):
    devices = {}
    tokens = {}

    def hosted(self, method, **params):
        if method in {'opc_pair', 'opc_status', 'opc_token', 'opc_revoke'}:
            action = {'opc_pair': 'pair', 'opc_status': 'status', 'opc_token': 'token', 'opc_revoke': 'revoke'}[method]
            claims = verify_opc_proof(params['proof'], origin=ORIGIN, action=action, now=NOW[0])
            key = (self.owner, claims['installation_id'])
            if method == 'opc_pair':
                if params['browser_digest'] not in self.authorized:
                    raise PermissionError('browser unavailable')
                self.devices.setdefault(key, 'pending')
                return {'installation_id': key[1], 'pairing_id': 'canonical_pair',
                        'session_id': 'consent_challenge', 'message_to_sign': 'Sign device consent',
                        'expires_at': NOW[0] + 600}
            if method == 'opc_status':
                return {'installation_id': key[1], 'status': self.devices.get(key, 'unpaired'),
                        'user_id': 'internal-user', 'wallet_identity_id': 'internal-wallet',
                        'spending_grant_id': 'internal-grant'}
            if method == 'opc_revoke':
                self.devices[key] = 'revoked'
                return {'installation_id': key[1], 'status': 'revoked'}
            if self.devices.get(key) != 'active':
                raise PermissionError('consent required')
            token = secrets.token_urlsafe(32)
            self.tokens[token] = (key, NOW[0] + 300)
            return {'installation_id': key[1], 'access_token': token, 'expires_at': NOW[0] + 300,
                    'token_type': 'Bearer', 'mcp_url': ORIGIN + '/mcp'}
        if method == 'opc_approve':
            if params['browser_digest'] not in self.authorized or params['signature'] != self.owner:
                raise PermissionError('wallet consent required')
            keys = [key for key in self.devices if key[0] == self.owner]
            assert len(keys) == 1
            self.devices[keys[0]] = 'active'
            return {'installation_id': keys[0][1]}
        if method == 'opc_authenticate':
            key, expires = self.tokens[params['access_token']]
            if key[0] != self.owner or self.devices[key] != 'active' or expires <= NOW[0]:
                raise PermissionError('credential revoked or expired')
            return {'issuer': 'opc', 'user_id': 'commerce-demo-user', 'agent_id': 'hermes',
                    'scope': 'payments', 'wallet_identity_id': 'wallet', 'spending_grant_id': 'grant',
                    'installation_id': key[1], 'credential_id': 'credential', 'expires_at': expires}
        return super().hosted(method, **params)

    def status(self):
        return {'owner': self.owner, 'onboarding': {'wallet_identity_id': 'wallet',
                'spending_grant_id': 'grant', 'budget_binding_id': 'budget', 'allowance_id': 'allowance'},
                'commerce': {'private_order': 'must stay in Agent'}, 'wallet_operations': {}}


NOW = [2000000000]


@pytest.fixture
def service(tmp_path):
    from examples.monad_commerce.external_opc import ExternalHostedCommerceService
    DeviceCanary.devices = {}
    DeviceCanary.tokens = {}
    NOW[0] = 2000000000
    with ExternalHostedCommerceService(tmp_path, configuration(), public_origin=ORIGIN,
                                      canary_factory=DeviceCanary, clock=lambda: NOW[0]) as value:
        yield value


def proof(key, action, request='request-0001'):
    return sign_opc_proof(key, origin=ORIGIN, action=action, request_id=request,
                          now=NOW[0], label='My Codex' if action == 'pair' else None)


def login(service, owner):
    browser, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    service.session(browser, csrf)
    challenge = service.login_challenge(browser, csrf, owner)
    service.login_verify(browser, csrf, challenge['session_id'], owner)
    return browser, csrf


def claim(service, key, owner=OWNER_A):
    pending = service.device_request('pair', proof(key, 'pair'))
    request = pending['verification_uri'].rsplit('/', 1)[1]
    browser, csrf = login(service, owner)
    service.claim_device(browser, csrf, request)
    return browser, csrf, request


def active(service, key, owner=OWNER_A):
    browser, csrf, request = claim(service, key, owner)
    assert service.device_request('status', proof(key, 'status'))['pairing_required'] is True
    service.device_request('pair', proof(key, 'pair', 'request-0002'))
    challenge = service.binding_status(browser, request)['challenge']
    service.approve_device(browser, csrf, request, challenge['session_id'], owner)
    return browser, csrf, request


def test_intent_is_idempotent_and_never_creates_core_or_credential(service):
    key = DeviceSigningKey.generate()
    value = service.device_request('pair', proof(key, 'pair'))
    assert service.device_request('pair', proof(key, 'pair')) == value
    assert value['status'] == 'pending'
    assert not service._cache and not DeviceCanary.devices
    assert service.device_request('status', proof(key, 'status'))['status'] == 'pending'
    with pytest.raises(PermissionError):
        service.device_request('token', proof(key, 'token'))


def test_expired_initial_proof_cannot_be_reused_after_browser_claim(service):
    key = DeviceSigningKey.generate()
    old = proof(key, 'pair')
    browser, csrf, request = claim(service, key)
    NOW[0] += 120
    with pytest.raises(ValueError):
        service.device_request('pair', old)
    assert service.device_request('status', proof(key, 'status'))['pairing_required'] is True
    service.device_request('pair', proof(key, 'pair', 'request-0002'))
    assert service.binding_status(browser, request)['status'] == 'challenge'
    with pytest.raises(PermissionError):
        service.device_request('token', proof(key, 'token'))


def test_wallet_binding_csrf_and_tenant_isolation(service):
    key = DeviceSigningKey.generate()
    browser, csrf, request = claim(service, key)
    other, other_csrf = login(service, OWNER_B)
    with pytest.raises(PermissionError):
        service.binding_status(other, request)
    with pytest.raises(PermissionError):
        service.claim_device(other, other_csrf, request)
    with pytest.raises(PermissionError):
        service.claim_device(browser, other_csrf, request)
    assert not DeviceCanary.devices


def test_remote_credential_routes_only_to_authenticated_core_and_rechecks_revoke(service):
    key = DeviceSigningKey.generate()
    browser, csrf, request = active(service, key)
    issued = service.device_request('token', proof(key, 'token'))
    assert service.device_request('status', proof(key, 'status')) == {
        'installation_id': issued['installation_id'], 'status': 'active'}
    identity = service.authenticate(issued['access_token'])
    assert identity.owner == OWNER_A
    assert 'access_token' not in str(service.binding_status(browser, request))
    assert 'commerce' not in service.dispatch(browser, None, 'status', {})
    for operation in ('search', 'preview', 'execute', 'purchase', 'recover_purchase', 'opc_prepare'):
        with pytest.raises(ValueError):
            service.dispatch(browser, csrf, operation, {})
    service.device_request('revoke', proof(key, 'revoke'))
    with pytest.raises(PermissionError):
        service.authenticate(issued['access_token'])


def test_link_expiry_and_unknown_or_wrong_action_proofs_fail_closed(service):
    key = DeviceSigningKey.generate()
    value = service.device_request('pair', proof(key, 'pair'))
    request = value['verification_uri'].rsplit('/', 1)[1]
    browser, csrf = login(service, OWNER_A)
    with pytest.raises(ValueError):
        service.device_request('token', proof(key, 'status'))
    NOW[0] += 601
    with pytest.raises(PermissionError):
        service.claim_device(browser, csrf, request)


def test_public_config_has_only_safe_derived_binding_fields(service):
    value = service.wallet_configuration()
    network = value['networks'][0]
    assert network['chain_id'] == 10143
    assert network['token_decimals'] == 6
    assert network['terms']['total_atomic'] == '1000000'
    assert 'aws' not in str(value).lower() and 'rpc_urls' not in str(value)
    assert 'nonce' not in str(value) and 'profile' not in str(value)


@pytest.mark.parametrize('operation', ['verify_claim', 'verify_approval', 'verify_revocation'])
def test_browser_verification_reaches_real_canary_with_original_hash(service, operation):
    from types import MethodType
    from examples.monad_commerce.hosted_canary import HostedCanary
    browser, csrf = login(service, OWNER_A)
    _, canary = service._authorize(browser, csrf, mutation=True)
    transaction_hash = '0x' + 'ab' * 32
    calls = []
    def recorded(kind, tx_hash):
        calls.append((kind, tx_hash))
        return {'verified': True, 'transaction_hash': tx_hash}
    canary._record_hash = recorded
    canary.wallet_operations = {'revocation': {'core_revoked': True}}
    setattr(canary, operation, MethodType(getattr(HostedCanary, operation), canary))
    result = service.dispatch(browser, csrf, operation, {'transaction_hash': transaction_hash})
    assert result['verified'] is True and calls[0][1] == transaction_hash


def test_lost_approval_response_recovers_from_core_without_signing_again(service):
    key = DeviceSigningKey.generate()
    browser, csrf, request = claim(service, key)
    service.device_request('pair', proof(key, 'pair', 'request-0002'))
    device = service._device(service.device_request('status', proof(key, 'status'))['installation_id'])
    # Core committed approval but the HTTP response/broker commit was lost.
    canary = service._bound_canary(device)
    canary.hosted('opc_approve', browser_digest=device['browser_digest'],
                  csrf_digest=device['csrf_digest'], challenge_id='consent_challenge', signature=OWNER_A)
    assert service.device_request('status', proof(key, 'status'))['status'] == 'active'
    assert service.device_request('token', proof(key, 'token'))['access_token']
    assert service.binding_status(browser, request)['status'] == 'active'


def test_unbound_intents_expire_without_exhausting_capacity(service):
    service.max_devices = 1
    first, second = DeviceSigningKey.generate(), DeviceSigningKey.generate()
    service.device_request('pair', proof(first, 'pair'))
    with pytest.raises(ValueError):
        service.device_request('pair', proof(second, 'pair'))
    NOW[0] += 601
    assert service.device_request('pair', proof(second, 'pair'))['status'] == 'pending'


def test_revoked_unbound_intent_releases_capacity_and_all_links(service):
    service.max_devices = 1
    first, second = DeviceSigningKey.generate(), DeviceSigningKey.generate()
    service.device_request('pair', proof(first, 'pair'))
    service.device_request('pair', proof(first, 'pair', 'request-second-link'))
    assert service.device_request('revoke', proof(first, 'revoke'))['status'] == 'revoked'
    assert not DeviceCanary.devices  # No Core authority ever existed.
    assert service.db.execute('SELECT COUNT(*) FROM external_links').fetchone()[0] == 0
    assert service.device_request('status', proof(first, 'status'))['status'] == 'unpaired'
    assert service.device_request('pair', proof(second, 'pair'))['status'] == 'pending'


def test_new_management_link_allows_same_wallet_after_browser_logout(service):
    key = DeviceSigningKey.generate()
    browser, csrf, request = active(service, key)
    service.logout(browser, csrf)
    new_link = service.device_request('pair', proof(key, 'pair', 'request-management'))
    new_request = new_link['verification_uri'].rsplit('/', 1)[1]
    other, other_csrf = login(service, OWNER_A)
    assert service.claim_device(other, other_csrf, new_request)['status'] == 'active'
    with pytest.raises(PermissionError):
        service.binding_status(other, request)


@pytest.mark.parametrize('start_challenge', [False, True])
def test_claimed_device_can_resume_in_new_same_wallet_browser(service, start_challenge):
    key = DeviceSigningKey.generate()
    browser, csrf, old_request = claim(service, key)
    if start_challenge:
        service.device_request('pair', proof(key, 'pair', 'request-challenge'))
    service.logout(browser, csrf)
    management = service.device_request('pair', proof(key, 'pair', 'request-management'))
    request = management['verification_uri'].rsplit('/', 1)[1]
    other, other_csrf = login(service, OWNER_A)
    assert service.claim_device(other, other_csrf, request)['status'] == 'claimed'
    service.device_request('pair', proof(key, 'pair', 'request-resume'))
    assert service.binding_status(other, request)['status'] == 'challenge'
    with pytest.raises(PermissionError):
        service.binding_status(other, old_request)


def test_credentials_do_not_grow_without_bound_per_device(service):
    key = DeviceSigningKey.generate()
    active(service, key)
    for index in range(10):
        service.device_request('token', proof(key, 'token', 'token-request-' + str(index)))
    assert service.db.execute('SELECT COUNT(*) FROM external_credentials').fetchone()[0] == 1
