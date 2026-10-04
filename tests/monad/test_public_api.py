import pytest
from fastapi.testclient import TestClient
from examples.monad_commerce.public_api import create_app


class FakeCanary:
    def __init__(self):
        self.calls = []
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def status(self): return {'mode': 'monad_testnet', 'owner': '0x'+'44'*20, 'onboarding': {'phase': 'wallet'}}
    def onboarding(self, operation, **params):
        self.calls.append((operation, params))
        return {'phase': 'wallet', 'message_to_sign': 'exact signed challenge'}
    def approval_transaction(self): return {'transaction': {'data': '0x095ea7b3'}}
    def verify_approval(self, tx_hash):
        self.calls.append(('approval', tx_hash))
        return {'verified': False, 'status': 'pending'}
    def request(self, method, args=None):
        self.calls.append((method, args))
        return {'purchase_id': 'order-1', 'state': 'payment_submitted'}
    def revoke_prepare(self): return {'core_revoked': True, 'chain_revoked': False}
    def verify_revocation(self, tx_hash): return {'core_revoked': True, 'chain_revoked': False}


@pytest.fixture
def client():
    runtime = FakeCanary()
    app = create_app(runtime_factory=lambda: runtime)
    with TestClient(app, base_url='http://127.0.0.1:8091') as client:
        yield client, runtime


def session(client):
    client.headers['origin'] = 'http://127.0.0.1:8091'
    result = client.post('/api/session', json={})
    assert result.status_code == 200
    assert 'HttpOnly' in result.headers['set-cookie']


def test_wallet_mutations_require_cookie_origin_and_host(client):
    http, runtime = client
    assert http.post('/api/wallet/challenge', json={}).status_code == 403
    http.headers['origin'] = 'http://127.0.0.1:8091'
    assert http.post('/api/wallet/challenge', json={}).status_code == 401
    session(http)
    assert http.post('/api/wallet/challenge', json={}).status_code == 200
    assert runtime.calls == [('wallet_challenge', {})]
    assert http.get('/api/status', headers={'host': 'evil.example'}).status_code == 403
    assert http.post('/api/wallet/challenge', json={}, headers={'origin':'https://evil.example'}).status_code == 403


def test_scope_cannot_be_overridden_by_browser(client):
    http, runtime = client
    session(http)
    response = http.post('/api/wallet/verify', json={'signature':'0x'+'11'*65, 'owner':'0x'+'99'*20})
    assert response.status_code == 422 and not runtime.calls
    assert http.post('/api/wallet/challenge', json={'owner': '0x'+'99'*20}).status_code == 422


def test_approval_pending_does_not_send_new_transaction(client):
    http, runtime = client
    session(http)
    tx_hash = '0x' + 'aa' * 32
    for _ in range(2):
        result = http.post('/api/allowance/verify', json={'transaction_hash':tx_hash})
        assert result.status_code == 200 and result.json()['status'] == 'pending'
    assert runtime.calls == [('approval', tx_hash), ('approval', tx_hash)]


def test_signatures_do_not_appear_in_error_response(client):
    http, runtime = client
    session(http)
    def fail(*args, **kwargs): raise RuntimeError('secret-value')
    runtime.onboarding = fail
    response = http.post('/api/wallet/verify', json={'signature': '0x'+'11'*65})
    assert response.status_code == 409 and 'secret-value' not in response.text


def test_no_public_bind_or_oversized_body(client):
    with pytest.raises(ValueError):
        create_app(origin='https://public.example', runtime_factory=FakeCanary)
    http, _ = client
    session(http)
    assert http.post('/api/preview', content='x'*262145).status_code == 413
    result = http.get('/api/status')
    assert result.headers['cache-control'] == 'no-store'
    assert "frame-ancestors 'none'" in result.headers['content-security-policy']


def test_existing_purchase_recovery_has_no_replacement_preview_input(client):
    http, runtime = client
    session(http)
    result = http.post('/api/purchases/order-1/recover', json={})
    assert result.status_code == 200
    assert runtime.calls == [('recover_purchase', {'purchase_id': 'order-1'})]
    result = http.post('/api/purchases/order-1/recover', json={'preview_id': 'replacement'})
    assert result.status_code == 422
    assert len(runtime.calls) == 1


def test_real_rejected_wallet_hash_returns_409_and_status_allows_correction(client, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from examples.monad_commerce import public_canary
    from agentonomy_commerce.wallet_transactions import InvalidWalletTransaction
    http, runtime = client
    canary = public_canary.PublicCanary.__new__(public_canary.PublicCanary)
    canary.wallet_operations = {}
    canary.operations_path = tmp_path / 'wallet-operations.json'
    canary.owner = '0x' + '44' * 20
    canary.network = SimpleNamespace(token='0x' + '11' * 20, executor='0x' + '22' * 20)
    wrong, correct = '0x' + 'aa' * 32, '0x' + 'bb' * 32
    def verify(*args, tx_hash, **kwargs):
        if tx_hash == wrong:
            raise InvalidWalletTransaction('canonical unrelated transaction')
        return None
    monkeypatch.setattr(public_canary, 'verify_wallet_transaction', verify)
    runtime.verify_approval = canary.verify_approval
    runtime.status = lambda: {'wallet_operations': canary.wallet_operations}
    session(http)
    response = http.post('/api/allowance/verify', json={'transaction_hash': wrong})
    assert response.status_code == 409
    rejected = http.get('/api/status').json()['wallet_operations']['approval']
    assert rejected['status'] == 'rejected' and 'transaction_hash' not in rejected
    response = http.post('/api/allowance/verify', json={'transaction_hash': correct})
    assert response.status_code == 200 and response.json()['status'] == 'pending'
    pending = http.get('/api/status').json()['wallet_operations']['approval']
    assert pending['status'] == 'pending' and pending['transaction_hash'] == correct
