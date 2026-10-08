"""Bounded operator tests: real signing bytes, adversarial public-RPC evidence."""
from copy import deepcopy
from dataclasses import asdict
import importlib
import importlib.util
import json
import stat

from eth_abi import encode
from eth_account import Account
from eth_keys import keys
from eth_utils import keccak
import pytest

import agentonomy_commerce.budget_observations as observations
from agentonomy_commerce.budget_network import NetworkConfig
from agentonomy_commerce.erc8004 import RegistryConfig

KEY = bytes.fromhex('11' * 32)  # fixed test-only key; never used on a public chain
OWNER = Account.from_key(KEY).address.lower()
ID = '0x8004a818bfb912233c491871b3d84c89a494bd9e'
REP = '0x8004b663056a597dffe9eccc1965a193b7388713'
IMPL_A, IMPL_B = '0x' + 'aa' * 20, '0x' + 'bb' * 20
CODE = '0x6001600055'
CODE_HASH = '0x' + keccak(bytes.fromhex(CODE[2:])).hex()
URI = 'https://review.agentonomy.xyz/agent.json'
PRICE = 102_000_000_000


def module():
    assert importlib.util.find_spec('scripts.monad.erc8004_register') is not None, 'bounded registration operator is missing'
    return importlib.import_module('scripts.monad.erc8004_register')


def test_registration_operator_exists():
    module()


@pytest.fixture
def setup(monkeypatch):
    op = module()
    monkeypatch.setattr(op, 'GAS_OWNER', OWNER)
    network = NetworkConfig(mode='monad_testnet', chain_id=10143,
        rpc_urls=op.RPC_URLS, token=op.TOKEN, executor=op.EXECUTOR, payee=OWNER,
        gas_limit=500_000, max_gas_price_wei=150_000_000_000)
    config = RegistryConfig.from_dict(dict(identity_registry=ID, reputation_registry=REP,
        agent_id=None, agent_owner=OWNER, agent_uri=URI,
        identity_code_hash=CODE_HASH, reputation_code_hash=CODE_HASH,
        identity_implementation=IMPL_A, identity_implementation_code_hash=CODE_HASH,
        reputation_implementation=IMPL_B, reputation_implementation_code_hash=CODE_HASH),
        network=network, origin=op.ORIGIN)
    plan = op.build_plan(network, config, gas_price_wei=PRICE)
    state = State(plan)
    signer = Signer()
    monkeypatch.setattr(observations, "RpcClient", state.factory)
    return op, plan, state, signer


class Signer:
    def __init__(self):
        self.gas_signer = self
        self.calls = 0
    def sign_digest(self, digest):
        self.calls += 1
        signature = keys.PrivateKey(KEY).sign_msg_hash(digest)
        return signature.r.to_bytes(32, 'big') + signature.s.to_bytes(32, 'big') + bytes([27 + signature.v])


class State:
    def __init__(self, plan):
        self.plan = plan
        self.sends = 0
        self.calls = []
        self.transaction = self.receipt = None
        self.fail_send = self.pending = False
        self.preflight_bad = None
        self.identity_wallet = OWNER
        self.receipt_code = CODE
        self.mutate = None
    def factory(self, url, *, writable=False):
        return Rpc(self, url, writable)


class Rpc:
    def __init__(self, state, url, writable):
        self.state, self.url, self.writable = state, url, writable
    def check_chain(self, chain_id):
        assert int(self.call("eth_chainId", []), 16) == chain_id
    def call(self, method, params):
        s = self.state
        s.calls.append((self.url, self.writable, method, deepcopy(params)))
        second = self.url.endswith('monadinfra.com')
        if method == 'eth_chainId': return hex(10143)
        if method == 'eth_getTransactionCount': return hex(8 if s.preflight_bad == 'nonce' and second else 5 + s.sends)
        if method == 'eth_gasPrice': return hex(PRICE + 1 if s.preflight_bad == 'price' else PRICE)
        if method == 'eth_getBalance': return hex(1 if s.preflight_bad == 'funding' else 10**19)
        if method == 'eth_estimateGas': return hex(600_000 if s.preflight_bad == 'estimate' else 200_000)
        if method == 'eth_getBlockByNumber':
            number = 103 if params[0] == 'finalized' else int(params[0], 16)
            block_hash = '0x' + f'{number:064x}'
            if s.sends and s.mutate == 'boundary' and second and number == 100: block_hash = '0x' + 'ff' * 32
            return dict(number=hex(number), hash=block_hash)
        if method == 'eth_getCode': return s.receipt_code if params[1] == hex(99) else CODE
        if method == 'eth_getStorageAt': return '0x' + '00' * 12 + (IMPL_A if params[0] == ID else IMPL_B)[2:]
        if method == 'eth_call':
            selector = params[0]['data'][2:10]
            values = {
                keccak(text='getIdentityRegistry()')[:4].hex(): (['address'], [ID]),
                keccak(text='ownerOf(uint256)')[:4].hex(): (['address'], [OWNER]),
                keccak(text='getAgentWallet(uint256)')[:4].hex(): (['address'], [s.identity_wallet]),
                keccak(text='tokenURI(uint256)')[:4].hex(): (['string'], [URI]),
            }
            return '0x' + encode(*values[selector]).hex()
        if method == 'eth_sendRawTransaction':
            assert self.writable
            raw = bytes.fromhex(params[0][2:])
            tx_hash = '0x' + keccak(raw).hex()
            s.sends += 1
            tx = s.plan['transaction']
            s.transaction = {key: (hex(value) if type(value) is int else value) for key, value in tx.items()}
            s.transaction.pop('data')
            s.transaction.update(input=tx['data'], hash=tx_hash, type='0x0',
                blockNumber=hex(99), blockHash='0x' + f'{99:064x}', transactionIndex='0x0')
            owner_topic = '0x' + '00' * 12 + OWNER[2:]
            registered = ['0x' + keccak(text='Registered(uint256,string,address)').hex(), '0x' + f'{7:064x}', owner_topic]
            transfer = ['0x' + keccak(text='Transfer(address,address,uint256)').hex(), '0x' + '00' * 32, owner_topic, '0x' + f'{7:064x}']
            logs = [dict(address=ID, topics=topics, data=data, removed=False,
                transactionHash=tx_hash, blockHash=s.transaction['blockHash'], blockNumber=hex(99),
                transactionIndex='0x0', logIndex=hex(index))
                for index, (topics, data) in enumerate([(registered, '0x' + encode(['string'], [URI]).hex()), (transfer, '0x')])]
            s.receipt = dict(transactionHash=tx_hash, blockNumber=hex(99), blockHash=s.transaction['blockHash'],
                transactionIndex='0x0', status='0x1', logs=logs, gasUsed=hex(200_000),
                effectiveGasPrice=hex(PRICE), **{'from': OWNER, 'to': ID})
            if s.fail_send: raise RuntimeError('secret-looking RPC exception must not be printed')
            return tx_hash
        if method == 'eth_getTransactionByHash':
            result = deepcopy(s.transaction)
            if result and s.mutate in {'nonce', 'gas', 'gasPrice'}: result[s.mutate] = hex(int(result[s.mutate], 16) + 1)
            return result
        if method == 'eth_getTransactionReceipt':
            if s.pending: return None
            result = deepcopy(s.receipt)
            if not result: return None
            if s.mutate == 'event': result['logs'][0]['topics'][1] = '0x' + f'{8:064x}'
            if s.mutate == 'owner': result['logs'][0]['topics'][2] = '0x' + '00' * 12 + 'ab' * 20
            if s.mutate == 'uri': result['logs'][0]['data'] = '0x' + encode(['string'], [URI + '/wrong']).hex()
            if s.mutate == 'removed': result['logs'][0]['removed'] = True
            if s.mutate == 'log_block': result['logs'][0]['blockHash'] = '0x' + 'ff' * 32
            if s.mutate == 'duplicate': result['logs'].append(deepcopy(result['logs'][0]))
            if s.mutate == 'status': result['status'] = '0x0'
            if s.mutate == 'event_index' and second: result['logs'][0]['logIndex'] = '0x2'
            if s.mutate == 'divergent' and second: result['logs'][0]['topics'][1] = '0x' + f'{8:064x}'
            return result
        raise AssertionError((method, params))


def test_default_is_offline_and_never_loads_signer(setup, tmp_path):
    op, plan, state, signer = setup
    result = op.run_registration(plan, tmp_path / 'journal.json', rpc_factory=state.factory,
        signer_factory=lambda: pytest.fail('default loaded signer'))
    assert result['status'] == 'validated'
    assert not state.calls and signer.calls == 0
    assert not (tmp_path / 'journal.json').exists()


@pytest.mark.parametrize('field,value', [('nonce', 6), ('chainId', 1), ('to', REP), ('value', 1),
    ('data', '0x00000000'), ('gas', 500001), ('gasPrice', 150_000_000_001), ('from', REP)])
def test_tampered_plan_never_signs_even_with_updated_hash(setup, tmp_path, field, value):
    op, plan, state, signer = setup
    plan['transaction'][field] = value
    plan['plan_hash'] = op.plan_hash(plan)
    with pytest.raises(ValueError): op.run_registration(plan, tmp_path / 'journal.json', execute=True,
        signer_factory=lambda: signer, rpc_factory=state.factory)
    assert signer.calls == state.sends == 0


@pytest.mark.parametrize('bad', ['nonce', 'price', 'funding', 'estimate'])
def test_preflight_blocks_before_aws_signing(setup, tmp_path, bad):
    op, plan, state, signer = setup
    state.preflight_bad = bad
    with pytest.raises(ValueError): op.run_registration(plan, tmp_path / 'journal.json', execute=True,
        signer_factory=lambda: signer, rpc_factory=state.factory)
    assert signer.calls == state.sends == 0


def test_success_is_proven_and_durable_replay_does_not_resign(setup, tmp_path):
    op, plan, state, signer = setup
    path = tmp_path / 'journal.json'
    result = op.run_registration(plan, path, execute=True, signer_factory=lambda: signer, rpc_factory=state.factory)
    assert result['status'] == 'complete' and result['agent_id'] == 7
    assert result['identity']['agent_wallet'] == OWNER
    assert result['two_rpc_verified'] is True
    assert state.sends == signer.calls == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert 'raw_transaction' not in json.dumps(result)
    saved = json.loads(path.read_text())
    assert saved['attempted'] is True and Account.recover_transaction(saved['raw_transaction']).lower() == OWNER
    replay = op.run_registration(plan, path, execute=True, rpc_factory=state.factory,
        signer_factory=lambda: pytest.fail('replay loaded signer'))
    assert replay['status'] == 'complete' and replay['transaction_hash'] == result['transaction_hash']
    assert state.sends == signer.calls == 1


def test_unknown_broadcast_only_reconciles_original_hash(setup, tmp_path):
    op, plan, state, signer = setup
    state.fail_send = state.pending = True
    path = tmp_path / 'journal.json'
    result = op.run_registration(plan, path, execute=True, signer_factory=lambda: signer, rpc_factory=state.factory)
    assert result['status'] == 'pending'
    replay = op.run_registration(plan, path, execute=True, signer_factory=lambda: pytest.fail('must not resign'), rpc_factory=state.factory)
    assert replay['status'] == 'pending' and replay['transaction_hash'] == result['transaction_hash']
    state.pending = False
    assert op.run_registration(plan, path, rpc_factory=state.factory)['status'] == 'complete'
    assert signer.calls == state.sends == 1


@pytest.mark.parametrize('tamper', ['nonce', 'gas', 'gasPrice', 'event', 'owner', 'uri', 'removed',
    'log_block', 'duplicate', 'status', 'divergent', 'boundary'])
def test_receipt_and_event_drift_never_reports_complete(setup, tmp_path, tamper):
    op, plan, state, signer = setup
    state.mutate = tamper
    result = op.run_registration(plan, tmp_path / 'journal.json', execute=True,
        signer_factory=lambda: signer, rpc_factory=state.factory)
    assert result['status'] == 'blocked' and state.sends == signer.calls == 1


def test_registry_code_at_receipt_block_must_match(setup, tmp_path):
    op, plan, state, signer = setup
    state.receipt_code = '0x6002'
    result = op.run_registration(plan, tmp_path / 'journal.json', execute=True,
        signer_factory=lambda: signer, rpc_factory=state.factory)
    assert result['status'] == 'blocked'


def test_current_receiving_wallet_drift_blocks_registration_completion(setup, tmp_path):
    op, plan, state, signer = setup
    state.identity_wallet = REP
    result = op.run_registration(plan, tmp_path / 'journal.json', execute=True,
        signer_factory=lambda: signer, rpc_factory=state.factory)
    assert result['status'] == 'blocked'


def test_journal_raw_tampering_cannot_resend(setup, tmp_path):
    op, plan, state, signer = setup
    state.pending = True
    path = tmp_path / 'journal.json'
    op.run_registration(plan, path, execute=True, signer_factory=lambda: signer, rpc_factory=state.factory)
    saved = json.loads(path.read_text())
    saved['raw_transaction'] = '0x01'
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError): op.run_registration(plan, path, execute=True,
        signer_factory=lambda: pytest.fail('tampered journal must not sign'), rpc_factory=state.factory)
    assert signer.calls == state.sends == 1


def test_journal_cannot_follow_symlink(setup, tmp_path):
    op, plan, state, signer = setup
    target = tmp_path / 'target'
    target.write_text('{}')
    link = tmp_path / 'link'
    link.symlink_to(target)
    with pytest.raises(ValueError): op.run_registration(plan, link, execute=True,
        signer_factory=lambda: signer, rpc_factory=state.factory)
    assert signer.calls == state.sends == 0


def test_same_agent_id_with_disagreeing_event_position_is_rejected(setup, tmp_path):
    op, plan, state, signer = setup
    state.mutate = 'event_index'
    result = op.run_registration(plan, tmp_path / 'journal.json', execute=True,
        signer_factory=lambda: signer, rpc_factory=state.factory)
    assert result['status'] == 'blocked'


@pytest.mark.parametrize('field,value', [('expected_role_arn', 'arn:aws:iam::793643674201:role/other'),
    ('gas_key_arn', 'arn:aws:kms:ap-southeast-1:793643674201:key/a96db10d-d617-4c66-82b1-23df370da2fa'),
    ('profile', 'agentonomy-dev')])
def test_operator_factory_rejects_unapproved_role_key_or_profile_before_aws(setup, tmp_path, monkeypatch, field, value):
    op, plan, state, signer = setup
    configuration = dict(profile='agentonomy-commerce-monad-role', region='ap-southeast-1',
        account_id='793643674201', expected_role_arn=op.ROLE, credential_directory='/var/lib/agentonomy-sign/aws',
        execution_key_arn=op.EXECUTION_KEY, gas_key_arn=op.GAS_KEY,
        scope=dict(network=plan['network'], owner='0x59899831691aa79507818961773497c751bffc8b',
            execution_address='0x968dbabb8dca19c4a8174a260cc40db66bdb7915', relayer_address=OWNER, nonce_min=5, nonce_max=13))
    configuration[field] = value
    path = tmp_path / 'signer.json'
    path.write_text(json.dumps(configuration))
    path.chmod(0o600)
    import agentonomy_commerce.signer_process as process
    monkeypatch.setattr(process, '_load_signer', lambda *_: pytest.fail('bad operator config reached AWS'))
    with pytest.raises(ValueError): op._signer_factory(path, plan)


def test_cli_failure_does_not_assert_unseen_broadcast_was_absent(setup, tmp_path, capsys):
    op, plan, state, signer = setup
    assert op.main(['--network-config', str(tmp_path / 'absent'), '--registry-config', str(tmp_path / 'absent'),
        '--plan', str(tmp_path / 'absent'), '--journal', str(tmp_path / 'absent')]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'blocked'
    assert 'signed' not in result and 'broadcast' not in result
    assert 'Traceback' not in json.dumps(result)


def test_prepare_does_not_overwrite_a_plan_created_before_lock_acquisition(setup, tmp_path, monkeypatch, capsys):
    from contextlib import contextmanager
    from types import SimpleNamespace
    op, plan, state, signer = setup
    network_path, registry_path, path = tmp_path / 'network.json', tmp_path / 'registry.json', tmp_path / 'plan.json'
    for file, value in ((network_path, {}), (registry_path, plan['registry'])):
        file.write_text(json.dumps(value)); file.chmod(0o600)
    monkeypatch.setattr(op, 'validate_bootstrap', lambda _: SimpleNamespace(network=NetworkConfig(**plan['network'])))
    original_lock = op._journal_lock
    @contextmanager
    def competing_prepare(target):
        target.write_text('{"original_plan": "preserve"}'); target.chmod(0o600)
        with original_lock(target): yield
    monkeypatch.setattr(op, '_journal_lock', competing_prepare)
    assert op.main(['--prepare', '--network-config', str(network_path), '--registry-config', str(registry_path),
        '--plan', str(path), '--gas-price-wei', str(PRICE)]) == 2
    assert json.loads(path.read_text()) == {'original_plan': 'preserve'}
    assert json.loads(capsys.readouterr().out)['status'] == 'blocked'


def test_malformed_registered_abi_reports_blocked_without_resend(setup, tmp_path):
    op, plan, state, signer = setup
    state.pending = True
    path = tmp_path / 'journal.json'
    op.run_registration(plan, path, execute=True, signer_factory=lambda: signer, rpc_factory=state.factory)
    state.receipt['logs'][0]['data'] = '0x01'
    state.pending = False
    assert op.run_registration(plan, path, rpc_factory=state.factory)['status'] == 'blocked'
    assert state.sends == signer.calls == 1


def test_nonce_changes_after_signing_preserve_exact_unattempted_bytes(setup, tmp_path):
    op, plan, state, signer = setup
    original_sign = signer.sign_digest
    def delayed_sign(digest):
        result = original_sign(digest)
        state.preflight_bad = 'nonce'
        return result
    signer.sign_digest = delayed_sign
    path = tmp_path / 'journal.json'
    with pytest.raises(ValueError): op.run_registration(plan, path, execute=True,
        signer_factory=lambda: signer, rpc_factory=state.factory)
    assert state.sends == 0 and signer.calls == 1
    saved = json.loads(path.read_text())
    assert saved['attempted'] is False
    state.preflight_bad = None
    result = op.run_registration(plan, path, execute=True,
        signer_factory=lambda: pytest.fail('saved bytes must not be resigned'), rpc_factory=state.factory)
    assert result['status'] == 'complete' and result['transaction_hash'] == saved['transaction_hash']
    assert state.sends == signer.calls == 1
