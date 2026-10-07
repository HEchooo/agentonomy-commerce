import json
import sys
import types
from datetime import UTC, datetime
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from examples.monad_commerce import hosted_process
from examples.monad_commerce.hosted_canary import HostedCanary
from examples.monad_commerce.hosted_process import dispatch_hosted, validate_hosted_bootstrap


class FakeCore:
    def __getattr__(self, name):
        return lambda **params: {'method': name, 'params': params}


def test_browser_fields_are_explicit_and_cannot_select_owner():
    core = FakeCore()
    result = dispatch_hosted(core, 'browser_challenge', {'browser_digest': 'a'*64, 'csrf_digest': 'b'*64})
    assert result['method'] == 'browser_challenge'
    with pytest.raises(ValueError):
        dispatch_hosted(core, 'browser_challenge', {'browser_digest': 'a'*64, 'csrf_digest': 'b'*64, 'owner': 'other'})


def test_authenticate_does_not_accept_browser_provided_principal():
    assert dispatch_hosted(FakeCore(), 'opc_authenticate', {'access_token': 'opaque'})['params'] == {'access_token': 'opaque'}
    with pytest.raises(ValueError):
        dispatch_hosted(FakeCore(), 'opc_authenticate', {'access_token': 'opaque', 'user_id': 'other'})


def test_unknown_method_rejected():
    with pytest.raises(ValueError):
        dispatch_hosted(FakeCore(), 'sign_transaction', {})


def test_hosted_bootstrap_requires_explicit_https_origin():
    with pytest.raises(ValueError):
        validate_hosted_bootstrap({'public_origin': 'http://example.com'})


@pytest.mark.parametrize('row,safe', [
    (None, True), ({'state': 'spending_reserved'}, True),
    ({'state': 'payment_submitted', 'budget_attempt': {'tx_hash': 'sealed'}}, False),
    ({'state': 'released', 'tx_hash': 'ambiguous'}, False),
    ({'state': 'spending_reserved', 'risk_prebroadcast_state': 'broadcasting'}, False),
])
def test_only_canonical_absence_of_attempt_can_abort(row, safe):
    @contextmanager
    def transaction():
        yield SimpleNamespace(by_purchase=lambda key: row)
    runtime = SimpleNamespace(owner_address='0x'+'11'*20,
                              funding_service=SimpleNamespace(ledger=SimpleNamespace(transaction=transaction)))
    proof = dispatch_hosted(runtime, 'payment_safety', {'purchase_id': 'purchase_a'})
    assert proof['no_broadcast'] is safe


def test_browser_resume_reads_the_canonical_core_repository(monkeypatch):
    """Resume must reconstruct the Core challenge, not call a Marketplace method."""
    import services.account_service.repository as repository_module
    import sqlalchemy

    class Field:
        def __eq__(self, other):
            return ('equals', other)

    class FakeRow:
        created_by_public_account_session_id = Field()
        purpose = Field()

    class Query:
        def where(self, *conditions):
            assert conditions == (
                ('equals', 'public-session'),
                ('equals', 'clink_wallet_identity'),
            )
            return self

    class SessionTransaction:
        def scalars(self, query):
            assert isinstance(query, Query)
            return self

        def all(self):
            return [object()]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    challenge = SimpleNamespace(
        account_session_id='challenge-1',
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    public_session = SimpleNamespace(public_account_session_id='public-session')
    runtime = SimpleNamespace(
        browser_resume=lambda **_: (_ for _ in ()).throw(AssertionError('wrong scope')),
        repository=SimpleNamespace(
            access_public_account_browser_session=lambda digest, now: public_session,
            sessions=lambda: SessionTransaction(),
            _account_session=lambda row: challenge,
        ),
        _browser_session=lambda *args, **kwargs: None,
        _validate_wallet_challenge=lambda session, session_id: None,
        account_service=SimpleNamespace(_canonical_message=lambda value: 'canonical-message'),
    )
    monkeypatch.setattr(repository_module, 'AccountSessionRow', FakeRow)
    monkeypatch.setattr(sqlalchemy, 'select', lambda model: Query())

    result = dispatch_hosted(
        runtime,
        'browser_resume',
        {'browser_digest': 'a' * 64, 'csrf_digest': 'b' * 64},
    )
    assert result == {
        'session_id': 'challenge-1',
        'message_to_sign': 'canonical-message',
        'expires_at': '2030-01-01T00:00:00+00:00',
        'signing_method': 'personal_sign',
    }


@pytest.mark.parametrize('purchase_id', ['bad', 'purchase_', 'purchase_../x', b'purchase_x'])
def test_payment_safety_rejects_noncanonical_purchase_ids_before_ledger_access(purchase_id):
    touched = []

    @contextmanager
    def transaction():
        touched.append(True)
        yield SimpleNamespace(by_purchase=lambda key: None)

    runtime = SimpleNamespace(
        owner_address='0x' + '11' * 20,
        funding_service=SimpleNamespace(ledger=SimpleNamespace(transaction=transaction)),
    )
    with pytest.raises(ValueError, match='invalid purchase scope'):
        dispatch_hosted(runtime, 'payment_safety', {'purchase_id': purchase_id})
    assert not touched


class _FakePipe:
    def __init__(self):
        self.closed = False
        self.writes = []

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        return None

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakePipe()
        self.stdout = _FakePipe()

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


def test_hosted_core_process_command_and_environment_are_scoped(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(arguments, **kwargs):
        captured['arguments'] = arguments
        captured['kwargs'] = kwargs
        return _FakeProcess()

    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'must-not-cross-process')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'must-not-cross-process')
    monkeypatch.setenv('PYTHONPATH', 'must-not-cross-process')
    monkeypatch.setattr(hosted_process.subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(hosted_process, 'validate_hosted_bootstrap', lambda value: None)

    from examples.monad_commerce.hosted_process import HostedCoreBridge

    bridge = HostedCoreBridge(tmp_path, {'deployment': {}, 'signer_configuration': {}, 'public_origin': 'https://review.example'})
    bridge._start()
    try:
        assert captured['arguments'] == [
            sys.executable, '-m', 'examples.monad_commerce.hosted_process',
            'core', str(tmp_path),
        ]
        env = captured['kwargs']['env']
        assert set(env) <= {'PATH', 'SYSTEMROOT', 'TMPDIR', 'LANG', 'LC_ALL', 'PYTHONUNBUFFERED'}
        assert 'AWS_ACCESS_KEY_ID' not in env
        assert 'AWS_SECRET_ACCESS_KEY' not in env
        assert 'PYTHONPATH' not in env
    finally:
        bridge.close()


def test_hosted_market_process_places_state_before_market_and_keeps_runtime_path(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(arguments, **kwargs):
        captured['arguments'] = arguments
        captured['kwargs'] = kwargs
        return _FakeProcess()

    monkeypatch.setenv('AWS_PROFILE', 'must-not-cross-process')
    monkeypatch.setenv('PYTHONPATH', 'must-not-cross-process')
    from examples.commerce import node
    monkeypatch.setattr(node.subprocess, 'Popen', fake_popen)

    bridge = node.MarketplaceBridge(
        tmp_path,
        worker_module='examples.monad_commerce.hosted_process',
        worker_args=('market',),
        timeout_seconds=240,
    )
    try:
        assert captured['arguments'] == [
            sys.executable, '-m', 'examples.monad_commerce.hosted_process',
            str(tmp_path), 'market',
        ]
        env = captured['kwargs']['env']
        assert env['PYTHONPATH'] == str(node.ROOT)
        assert env['PYTHONUNBUFFERED'] == '1'
        assert 'AWS_PROFILE' not in env
    finally:
        bridge.close()


class _InputBuffer:
    def __init__(self, lines):
        self.lines = iter(lines)

    def readline(self, _limit):
        return next(self.lines, b'')


class _FakeInput:
    def __init__(self, lines):
        self.buffer = _InputBuffer(lines)


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def bind(self, address):
        assert address[0] == '127.0.0.1'

    def getsockname(self):
        return ('127.0.0.1', 32123)


def test_marketplace_worker_does_not_dispatch_core_methods(monkeypatch, tmp_path, capsys):
    class FakeRuntime:
        def __init__(self, state_dir, port, bootstrap):
            assert state_dir == tmp_path
            assert port == 32123
            self.bootstrap = bootstrap

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def search(self, **args):
            return {'scope': 'market', 'args': args}

    market_module = types.ModuleType('examples.monad_commerce.hosted_market_runtime')
    market_module.HostedCommerceRuntime = FakeRuntime
    monkeypatch.setitem(sys.modules, 'examples.monad_commerce.hosted_market_runtime', market_module)
    monkeypatch.setattr(hosted_process, 'validate_hosted_bootstrap', lambda value: None)
    monkeypatch.setattr(hosted_process.socket, 'socket', lambda: _FakeSocket())
    lines = [
        (json.dumps({'id': 1, 'method': 'initialize', 'arguments': {}}) + '\n').encode(),
        (json.dumps({'id': 2, 'method': 'search', 'arguments': {'query': 'csv'}}) + '\n').encode(),
        (json.dumps({'id': 3, 'method': 'reserve', 'arguments': {}}) + '\n').encode(),
    ]
    monkeypatch.setattr(hosted_process.sys, 'stdin', _FakeInput(lines))

    assert hosted_process._market_main(tmp_path) == 0
    responses = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert responses[0] == {'id': 1, 'result': {'status': 'ready', 'mode': 'monad_testnet'}}
    assert responses[1] == {'id': 2, 'result': {'scope': 'market', 'args': {'query': 'csv'}}}
    assert responses[2] == {'id': 3, 'error': 'Operation unconfirmed; query original order'}


def test_core_worker_does_not_dispatch_market_methods(monkeypatch, tmp_path, capsys):
    import examples.monad_commerce.hosted_signer as signer_module
    import examples.monad_commerce.core_worker as core_worker
    import examples.monad_commerce.hosted_core as core_module

    class FakeSigner:
        def __init__(self, configuration):
            self.configuration = configuration

        def close(self):
            return None

    class FakeRuntime:
        def __init__(self, state_dir, deployment, signer, **kwargs):
            assert state_dir == tmp_path
            assert deployment == {}
            assert signer.configuration == {}

        def close(self):
            return None

    monkeypatch.setattr(signer_module, 'HostedKmsSignerBridge', FakeSigner)
    monkeypatch.setattr(core_module, 'HostedWalletCore', FakeRuntime)
    monkeypatch.setattr(core_worker, 'METHODS', {'reserve'})
    monkeypatch.setattr(core_worker, '_dispatch', lambda runtime, method, params: {'scope': 'core'})
    monkeypatch.setattr(hosted_process, 'validate_hosted_bootstrap', lambda value: None)
    lines = [
        (json.dumps({'deployment': {}, 'signer_configuration': {}, 'public_origin': 'https://review.example'}) + '\n').encode(),
        (json.dumps({'id': 1, 'method': 'reserve', 'params': {}}) + '\n').encode(),
        (json.dumps({'id': 2, 'method': 'search', 'params': {}}) + '\n').encode(),
    ]
    monkeypatch.setattr(hosted_process.sys, 'stdin', _FakeInput(lines))

    assert hosted_process._core_main(tmp_path) == 0
    responses = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert responses[0] == {'id': 1, 'ok': True, 'result': {'scope': 'core'}}
    assert responses[1]['id'] == 2
    assert responses[1]['ok'] is False


def test_hosted_canary_requires_complete_two_rpc_payment_proof():
    canary = HostedCanary.__new__(HostedCanary)
    canary.tenant_id = 'tenant_a'
    canary.owner = '0x' + '11' * 20
    canary.network = SimpleNamespace(
        chain_id=10143,
        token='0x' + '22' * 20,
        executor='0x' + '33' * 20,
        payee='0x' + '44' * 20,
    )
    evidence = {
        'verified': True,
        'status': 'verified',
        'executor': canary.network.executor,
        'owner': canary.owner,
        'amount_atomic': '300000',
        'purchase_commitment': '0x' + 'aa' * 32,
        'quote_commitment': '0x' + 'bb' * 32,
        'grant_hash': '0x' + 'cc' * 32,
        'block_hash': '0x' + 'dd' * 32,
        'finality_block_hash': '0x' + 'ee' * 32,
        # two_rpc_verified, chain_id, receipt_status, transaction_hash,
        # token and payee are intentionally absent.
    }
    with pytest.raises(ValueError, match='incomplete'):
        canary._verified_gate_proof(
            {'purchase_id': 'purchase_a', 'settlement': evidence}, 'purchase_a'
        )
