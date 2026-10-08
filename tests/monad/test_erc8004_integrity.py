"""Reject registry drift and feedback evidence before publishing anything."""
from copy import deepcopy

from eth_abi import encode
import pytest

import agentonomy_commerce.erc8004 as erc8004
from test_erc8004 import (
    FakeRpc, State, _client, _feedback_fixture, _selector, config, network,
    BLOCK, BLOCK_HASH, IDENTITY, REPUTATION, OWNER, BUYER, TX_HASH, CODE, IDENTITY_CODE_HASH,
)


class BoundRpc(FakeRpc):
    def call(self, method, params):
        if method == 'eth_call' and params[0]['to'].lower() == REPUTATION:
            if params[0]['data'][2:10] == _selector('getIdentityRegistry()'):
                binding = getattr(self.state, 'binding', IDENTITY)
                return '0x' + encode(['address'], [binding]).hex()
        if method == 'eth_getCode' and params[1] == hex(BLOCK - 1):
            return getattr(self.state, 'receipt_code', CODE)
        if method == 'eth_getBlockByNumber' and params[0] == hex(BLOCK - 1):
            return {'number': hex(BLOCK - 1), 'hash': BLOCK_HASH}
        return super().call(method, params)


def bound_client(states):
    return erc8004.ERC8004Client(network(), config(), client_factory=lambda url:
                               BoundRpc(states[0 if url == network().rpc_urls[0] else 1], url))


@pytest.mark.parametrize('states', [(OWNER, OWNER), (IDENTITY, OWNER)])
def test_reputation_must_bind_the_configured_identity_registry(states):
    a, b = State(), State()
    a.binding, b.binding = states
    with pytest.raises(ValueError, match='[Ii]dentity registry'):
        bound_client((a, b)).verify_identity()


@pytest.mark.parametrize('field,value', [
    ('identity_owner', BUYER), ('identity_wallet', BUYER), ('identity_uri', 'https://other.example/agent.json'),
    ('code', {IDENTITY: '0x6001', REPUTATION: CODE}),
])
def test_identity_drift_is_rejected(field, value):
    state = State()
    setattr(state, field, value)
    with pytest.raises(ValueError):
        _client(state).verify_identity()


def test_identity_rpc_disagreement_is_rejected():
    a, b = State(), State()
    b.identity_owner = BUYER
    with pytest.raises(ValueError, match='disagree'):
        bound_client((a, b)).verify_identity()


def test_proxy_implementation_slot_and_code_hash_are_pinned():
    implementation_a, implementation_b = '0x' + '88' * 20, '0x' + '99' * 20
    state = State()
    state.storage = {IDENTITY: '0x' + '00' * 12 + implementation_a[2:],
                     REPUTATION: '0x' + '00' * 12 + implementation_b[2:]}
    state.code = {IDENTITY: CODE, REPUTATION: CODE, implementation_a: CODE, implementation_b: CODE}
    pins = config(identity_implementation=implementation_a,
                  reputation_implementation=implementation_b,
                  identity_implementation_code_hash=IDENTITY_CODE_HASH,
                  reputation_implementation_code_hash=IDENTITY_CODE_HASH)
    client = _client(state, cfg=pins)
    assert client.verify_identity()['verified'] is True
    state.storage[IDENTITY] = state.storage[REPUTATION]
    with pytest.raises(ValueError, match='implementation address'):
        client.verify_identity()
    state.storage[IDENTITY] = '0x' + '00' * 12 + implementation_a[2:]
    state.code[implementation_a] = '0x6001'
    with pytest.raises(ValueError, match='implementation runtime code hash'):
        client.verify_identity()


def test_boundary_reorg_during_registry_reads_is_rejected():
    class ReorgRpc(BoundRpc):
        reads = 0
        def call(self, method, params):
            result = super().call(method, params)
            if method == 'eth_getBlockByNumber' and params[0] == hex(BLOCK):
                self.reads += 1
                if self.reads > 1:
                    return result | {'hash': '0x' + 'ff' * 32}
            return result
    client = erc8004.ERC8004Client(network(), config(), client_factory=lambda url: ReorgRpc(State(), url))
    with pytest.raises(ValueError, match='changed'):
        client.verify_identity()


def observations(state, *, transaction_block=BLOCK):
    tx, receipt = deepcopy(state.transaction), deepcopy(state.receipt)
    tx['blockNumber'] = receipt['blockNumber'] = hex(transaction_block)
    for log in receipt['logs']:
        log['blockNumber'] = hex(transaction_block)
    row = {'chain_id': hex(31337), 'transaction': tx, 'receipt': receipt,
           'canonical_block': {'number': hex(transaction_block), 'hash': BLOCK_HASH},
           'finality': {'kind': 'local', 'verified': True,
                        'canonical_block': {'number': hex(BLOCK), 'hash': BLOCK_HASH}}}
    return [deepcopy(row), deepcopy(row)]


def test_feedback_checks_code_at_receipt_block_not_only_later_boundary(monkeypatch):
    state = State()
    transaction = _feedback_fixture(state)
    state.receipt_code = '0x6001'
    monkeypatch.setattr(erc8004, 'fetch_observations', lambda *_: observations(state, transaction_block=BLOCK - 1))
    with pytest.raises(ValueError, match='code hash'):
        bound_client((state, state)).verify_feedback(buyer=BUYER, tx_hash=TX_HASH, transaction=transaction)


@pytest.mark.parametrize('tamper', ['sender', 'recipient', 'value', 'calldata', 'status', 'event', 'divergent', 'state'])
def test_feedback_rejects_wrong_or_disagreeing_chain_evidence(monkeypatch, tamper):
    state = State()
    transaction = _feedback_fixture(state)
    rows = observations(state)
    for row in rows:
        if tamper == 'sender': row['transaction']['from'] = OWNER
        if tamper == 'recipient': row['transaction']['to'] = IDENTITY
        if tamper == 'value': row['transaction']['value'] = '0x1'
        if tamper == 'calldata': row['transaction']['input'] = row['transaction']['data'] = '0x00000000'
        if tamper == 'status': row['receipt']['status'] = '0x0'
        if tamper == 'event': row['receipt']['logs'][0]['topics'][1] = '0x' + '00' * 31 + '01'
    if tamper == 'divergent': rows[1]['transaction']['nonce'] = '0x2'
    if tamper == 'state': state.score = 42
    monkeypatch.setattr(erc8004, 'fetch_observations', lambda *_: rows)
    with pytest.raises(ValueError):
        bound_client((state, state)).verify_feedback(buyer=BUYER, tx_hash=TX_HASH, transaction=transaction)


def test_registry_cli_never_reports_pending_identity_as_verified(tmp_path, monkeypatch, capsys):
    import json
    from scripts.monad import erc8004_registry as cli
    from test_hosted_service import configuration, ORIGIN
    commerce, registry = tmp_path / 'commerce.json', tmp_path / 'registry.json'
    commerce.write_text(json.dumps(configuration()))
    registry.write_text('{}')
    commerce.chmod(0o600)
    registry.chmod(0o600)
    class Config:
        @classmethod
        def from_dict(cls, *args, **kwargs): return object()
    class Client:
        def __init__(self, *args): pass
        def verify_identity(self): return None
    monkeypatch.setattr(cli, 'RegistryConfig', Config)
    monkeypatch.setattr(cli, 'ERC8004Client', Client)
    assert cli.main(['--config', str(registry), '--network-config', str(commerce),
                     '--origin', ORIGIN, '--operation', 'verify-identity']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'registration_pending'
    assert result['signed'] is False and result['broadcast'] is False
