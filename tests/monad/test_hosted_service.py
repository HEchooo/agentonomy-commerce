import hashlib
import sqlite3
from types import SimpleNamespace
import pytest
from examples.monad_commerce.hosted_service import HostedCommerceService

ORIGIN = 'https://commerce.example.org'
OWNER_A, OWNER_B = ['0x' + c*40 for c in 'ab']


def configuration():
    network = {'mode': 'monad_testnet', 'chain_id': 10143,
               'rpc_urls': ['https://rpc.example.org', 'https://rpc.example.net'],
               'token': '0x'+'11'*20, 'executor': '0x'+'22'*20, 'payee': '0x'+'33'*20}
    signer = {'profile': 'hackathon', 'region': 'ap-southeast-1', 'account_id': '123456789012',
              'expected_role_arn': 'arn:aws:iam::123456789012:role/test-runtime',
              'credential_directory': '/tmp/isolated-test-session',
              'execution_key_arn': 'arn:aws:kms:ap-southeast-1:123456789012:key/11111111-1111-4111-8111-111111111111',
              'gas_key_arn': 'arn:aws:kms:ap-southeast-1:123456789012:key/22222222-2222-4222-8222-222222222222',
              'scope': {'network': network, 'owner': OWNER_A, 'execution_address': '0x'+'44'*20,
                        'relayer_address': '0x'+'55'*20, 'nonce_min': 3, 'nonce_max': 5}}
    return {'deployment': network | {'owner': OWNER_A, 'execution_signer': '0x'+'44'*20, 'relayer': '0x'+'55'*20},
            'signer_configuration': signer}


class Canary:
    created = []
    def __init__(self, path, config, **kwargs):
        self.path, self.config, self.kwargs = path, config, kwargs
        self.owner = config['deployment']['owner']
        self.calls, self.authorized = [], set()
        self.fail_authorize = False
        Canary.created.append(self)
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def hosted(self, method, **params):
        self.calls.append((method, params))
        digest = params.get('browser_digest')
        if method == 'browser_resume':
            return None
        if method == 'browser_challenge':
            return {'session_id': 'challenge_a', 'message_to_sign': self.owner, 'expires_at': '2099-01-01T00:00:00Z'}
        if method == 'browser_verify':
            if params['signature'] != self.owner:
                raise ValueError('invalid wallet proof')
            self.authorized.add(digest)
            return {'wallet_address': self.owner}
        if method == 'browser_authorize':
            if self.fail_authorize:
                raise RuntimeError('Core authorization was revoked')
            if digest not in self.authorized:
                raise ValueError('unauthenticated')
            return {'wallet_address': self.owner}
        if method == 'browser_logout':
            self.authorized.discard(digest)
            return {'status': 'logged_out'}
        if method == 'opc_status': return {'status': 'unpaired'}
        raise AssertionError(method)
    def status(self): return {'owner': self.owner, 'onboarding': {'phase': 'grant'}}
    def request(self, method, args):
        self.calls.append((method, args))
        return {'purchase_id': args['purchase_id'], 'settlement': {'owner': self.owner}}


class PersistentCanary(Canary):
    """A tenant-scoped fake whose orders survive runtime replacement."""

    created = []
    orders_by_tenant = {}
    authorized_by_tenant = {}

    def __init__(self, path, config, **kwargs):
        self.path, self.config, self.kwargs = path, config, kwargs
        self.owner = config['deployment']['owner']
        self.tenant_id = kwargs['tenant_id']
        self.calls = []
        self.authorized = self.authorized_by_tenant.setdefault(self.tenant_id, set())
        self.orders = self.orders_by_tenant.setdefault(self.tenant_id, {})
        self.fail_authorize = False
        self.closed = False
        self.broken = False
        self.market = SimpleNamespace(broken=False)
        self.execute_calls = []
        self.recover_calls = []
        type(self).created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def request(self, method, args):
        self.calls.append((method, args))
        purchase_id = args.get('purchase_id')
        if method == 'purchase':
            if purchase_id not in self.orders:
                raise KeyError('purchase_not_found')
            return dict(self.orders[purchase_id])
        if method == 'recover_purchase':
            self.recover_calls.append(purchase_id)
            if purchase_id not in self.orders:
                raise KeyError('purchase_not_found')
            return dict(self.orders[purchase_id])
        if method == 'execute':
            self.execute_calls.append(args['preview_id'])
            raise AssertionError('a read/recovery must not auto-execute')
        raise AssertionError(method)


@pytest.fixture
def persistent_service(tmp_path):
    PersistentCanary.created = []
    PersistentCanary.orders_by_tenant = {}
    PersistentCanary.authorized_by_tenant = {}
    with HostedCommerceService(tmp_path, configuration(), public_origin=ORIGIN,
                               canary_factory=PersistentCanary, clock=lambda: 1000) as service:
        yield service


class CommitOnceFailure:
    """Connection proxy that fails one commit after Core accepted proof."""

    def __init__(self, connection):
        self.connection = connection
        self.fail_next_commit = True

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        if self.fail_next_commit:
            self.fail_next_commit = False
            self.connection.rollback()
            raise sqlite3.OperationalError('simulated registry crash')
        return self.connection.commit()


@pytest.fixture
def service(tmp_path):
    Canary.created = []
    with HostedCommerceService(tmp_path, configuration(), public_origin=ORIGIN,
                               canary_factory=Canary, clock=lambda: 1000) as service:
        yield service


def login(service, token, csrf, owner):
    service.session(token, csrf)
    challenge = service.login_challenge(token, csrf, owner)
    service.login_verify(token, csrf, challenge['session_id'], owner)


def test_address_is_not_login_and_crosswallet_order_reads_are_routed_by_session(service):
    service.session('x'*43, 'y'*43)
    service.login_challenge('x'*43, 'y'*43, OWNER_A)
    with pytest.raises(PermissionError):
        service.dispatch('x'*43, None, 'purchase', {'purchase_id': 'purchase_a'})
    service.login_verify('x'*43, 'y'*43, 'challenge_a', OWNER_A)
    login(service, 'q'*43, 'r'*43, OWNER_B)
    assert service.dispatch('x'*43, None, 'purchase', {'purchase_id': 'purchase_a'})['settlement']['owner'] == OWNER_A
    assert service.dispatch('q'*43, None, 'purchase', {'purchase_id': 'purchase_a'})['settlement']['owner'] == OWNER_B
    assert Canary.created[0].path != Canary.created[1].path
    assert Canary.created[0].kwargs['gate'] is Canary.created[1].kwargs['gate']
    assert Canary.created[1].config['signer_configuration']['scope']['owner'] == OWNER_B
    assert Canary.created[1].config['deployment']['domain'] == 'commerce.example.org'


def test_csrf_and_expiry_and_logout_are_checked_on_every_operation(service):
    login(service, 'x'*43, 'y'*43, OWNER_A)
    with pytest.raises(PermissionError): service.dispatch('x'*43, 'z'*43, 'grant_challenge', {})
    service.logout('x'*43, 'y'*43)
    with pytest.raises(PermissionError): service.dispatch('x'*43, None, 'purchase', {'purchase_id': 'purchase_a'})
    login(service, 'q'*43, 'r'*43, OWNER_B)
    service.clock = lambda: 5000
    with pytest.raises(PermissionError): service.dispatch('q'*43, None, 'status', {})


def test_session_registry_contains_only_digests_and_cannot_rebind_live_owner(service):
    login(service, 'x'*43, 'y'*43, OWNER_A)
    with pytest.raises(PermissionError): service.login_challenge('x'*43, 'y'*43, OWNER_B)
    with pytest.raises(PermissionError): service.session('x'*43, 'z'*43)
    raw = (service.state_dir / 'sessions.sqlite3').read_bytes()
    assert b'x'*43 not in raw and b'y'*43 not in raw
    assert hashlib.sha256(('x'*43).encode()).hexdigest().encode() in raw


def test_anonymous_admission_is_bounded(service):
    service.max_sessions = 2
    service.session('x'*43, 'y'*43)
    service.session('q'*43, 'r'*43)
    with pytest.raises(ValueError): service.session('s'*43, 't'*43)


def test_browser_authorization_is_rechecked_for_read_mutation_and_revoke_race(service):
    login(service, 'x'*43, 'y'*43, OWNER_A)
    canary = Canary.created[-1]
    canary.fail_authorize = True

    with pytest.raises(PermissionError):
        service.dispatch('x'*43, None, 'purchase', {'purchase_id': 'purchase_a'})
    with pytest.raises(PermissionError):
        service.dispatch('x'*43, 'y'*43, 'revoke_prepare', {})

    authorize_calls = [method for method, _ in canary.calls
                       if method == 'browser_authorize']
    assert len(authorize_calls) == 2


def test_tenant_id_is_pinned_to_owner_before_state_path_is_opened(service, tmp_path):
    login(service, 'x'*43, 'y'*43, OWNER_A)
    canary_count = len(Canary.created)
    row = service.db.execute(
        'SELECT browser_digest FROM sessions WHERE owner=?', (OWNER_A,)
    ).fetchone()
    tampered_tenant = 'tenant_' + 'c' * 32
    service.db.execute('UPDATE sessions SET tenant_id=? WHERE browser_digest=?',
                       (tampered_tenant, row['browser_digest']))
    service.db.commit()

    with pytest.raises((PermissionError, ValueError)):
        service.dispatch('x'*43, None, 'purchase', {'purchase_id': 'purchase_a'})
    assert len(Canary.created) == canary_count
    assert not (tmp_path / 'tenants' / tampered_tenant).exists()


def test_broken_cached_runtime_is_evicted_without_auto_reexecute(persistent_service):
    token, csrf = 'x'*43, 'y'*43
    login(persistent_service, token, csrf, OWNER_A)
    first = PersistentCanary.created[-1]
    order = {
        'purchase_id': 'purchase_a', 'state': 'delivered',
        'service_result': {'rows': 1},
    }
    first.orders[order['purchase_id']] = order

    assert persistent_service.dispatch(token, None, 'purchase',
                                      {'purchase_id': 'purchase_a'}) == order
    first.broken = True
    first.market.broken = True

    read = persistent_service.dispatch(token, None, 'purchase',
                                       {'purchase_id': 'purchase_a'})
    recovered = persistent_service.dispatch(token, csrf, 'recover_purchase',
                                            {'purchase_id': 'purchase_a'})
    assert read == order
    assert recovered == order
    assert len(PersistentCanary.created) == 2
    assert first.closed
    assert PersistentCanary.created[-1].recover_calls == ['purchase_a']
    assert not PersistentCanary.created[-1].execute_calls


def test_login_verify_recovers_after_core_proof_then_registry_commit_failure(service):
    token, csrf = 'x'*43, 'y'*43
    service.session(token, csrf)
    challenge = service.login_challenge(token, csrf, OWNER_A)
    service.db = CommitOnceFailure(service.db)

    with pytest.raises(sqlite3.OperationalError, match='registry crash'):
        service.login_verify(token, csrf, challenge['session_id'], OWNER_A)

    assert service.session(token, csrf)['authenticated'] is True
    result = service.dispatch(token, None, 'purchase', {'purchase_id': 'purchase_a'})
    assert result['settlement']['owner'] == OWNER_A
    row = service.db.execute(
        'SELECT authenticated FROM sessions WHERE browser_digest=?',
        (hashlib.sha256(token.encode()).hexdigest(),),
    ).fetchone()
    assert row['authenticated'] == 1
