"""One bounded ERC-8004 testnet registration; never reachable from the web signer.

No default signing, ambient AWS profile, arbitrary calldata or replacement nonce.
Attempted submissions are only reconciled against the original private journal.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re

from eth_abi import decode, encode
from eth_account import Account
from eth_account._utils.legacy_transactions import Transaction
from eth_utils import keccak
import rlp

from agentonomy_commerce.budget_network import NetworkConfig, RpcClient, address, quantity
from agentonomy_commerce.erc8004 import ERC8004Client, RegistryConfig
from agentonomy_commerce.kms_adapter import sign_legacy_transaction
from agentonomy_commerce.wallet_transactions import transaction_hash, verify_wallet_transaction
from examples.monad_commerce.hosted_server import load_configuration
from examples.monad_commerce.public_worker import validate_bootstrap
from scripts.monad.faucet_deploy import (
    OperatorError, _journal_lock, _write_atomic, _sanitized_aws_environment,
)

CHAIN_ID = 10143
ORIGIN = 'https://review.agentonomy.xyz'
RPC_URLS = ('https://testnet-rpc.monad.xyz', 'https://rpc-testnet.monadinfra.com')
GAS_OWNER = '0xbdcb39ac5ff83485cb35160f0ddaa0b7446ee009'
TOKEN = '0x1bf06ce9eeeb9e998cecf96cd46f1a7e5bed547a'
EXECUTOR = '0x7a87b04c67c11afa7ce1a27bdb3c1c1ca55e1aa4'
IDENTITY = '0x8004a818bfb912233c491871b3d84c89a494bd9e'
REPUTATION = '0x8004b663056a597dffe9eccc1965a193b7388713'
NONCE = 5
MAX_GAS = 500_000
MAX_PRICE = 150_000_000_000
ROLE = 'arn:aws:iam::793643674201:role/agentonomy-dev-kms-runtime'
KEY_PREFIX = 'arn:aws:kms:ap-southeast-1:793643674201:key/'
EXECUTION_KEY = KEY_PREFIX + 'd7be91fa-8f65-461a-aa3a-0e3affedc609'
GAS_KEY = KEY_PREFIX + '8f24151c-0464-4a6c-b0a8-6cacd21c6caa'
REGISTER_TOPIC = '0x' + keccak(text='Registered(uint256,string,address)').hex()
TRANSFER_TOPIC = '0x' + keccak(text='Transfer(address,address,uint256)').hex()
PLAN_SCHEMA = 'monad-erc8004-registration-plan-v1'
JOURNAL_SCHEMA = 'monad-erc8004-registration-journal-v1'


def plan_hash(plan: Mapping) -> str:
    value = {key: item for key, item in plan.items() if key != 'plan_hash'}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True).encode()).hexdigest()


def _scope(network, registry):
    if (network.mode != 'monad_testnet' or network.chain_id != CHAIN_ID
        or tuple(network.rpc_urls) != RPC_URLS or network.token != TOKEN
        or network.executor != EXECUTOR or network.payee != GAS_OWNER
        or network.gas_limit != MAX_GAS or network.max_gas_price_wei != MAX_PRICE
        or registry.identity_registry != IDENTITY or registry.reputation_registry != REPUTATION
        or registry.agent_owner != GAS_OWNER or registry.agent_id is not None
        or registry.agent_uri != ORIGIN + '/agent.json'):
        raise ValueError('registration is outside the fixed public rollout scope')


def build_plan(network, registry, *, gas_price_wei):
    _scope(network, registry)
    # Revalidate the config/network binding and complete proxy pins, offline.
    RegistryConfig.from_dict(asdict(registry), network=network, origin=ORIGIN)
    if type(gas_price_wei) is not int or not 0 < gas_price_wei <= MAX_PRICE:
        raise ValueError('registration gas price exceeds the fixed cap')
    data = '0x' + (keccak(text='register(string)')[:4] + encode(['string'], [registry.agent_uri])).hex()
    network_value = asdict(network)
    network_value['rpc_urls'] = list(network.rpc_urls)
    value = dict(schema_version=PLAN_SCHEMA, origin=ORIGIN, network=network_value,
        registry=asdict(registry), transaction=dict(chainId=CHAIN_ID, nonce=NONCE,
        to=IDENTITY, value=0, data=data, gas=MAX_GAS, gasPrice=gas_price_wei,
        **{'from': GAS_OWNER}))
    value['plan_hash'] = plan_hash(value)
    return value


def validate_plan(plan):
    if not isinstance(plan, Mapping) or set(plan) != {
        'schema_version', 'origin', 'network', 'registry', 'transaction', 'plan_hash'}:
        raise ValueError('registration plan has unknown or missing fields')
    if plan['schema_version'] != PLAN_SCHEMA or plan['origin'] != ORIGIN:
        raise ValueError('registration plan schema/origin mismatch')
    if not isinstance(plan['transaction'], Mapping):
        raise ValueError('registration transaction required')
    network = NetworkConfig(**plan['network'])
    registry = RegistryConfig.from_dict(plan['registry'], network=network, origin=ORIGIN)
    expected = build_plan(network, registry, gas_price_wei=plan['transaction'].get('gasPrice'))
    # Python bool == int; explicitly enforce every unsigned quantity's type.
    if any(type(plan['transaction'].get(key)) is not int for key in ('chainId', 'nonce', 'value', 'gas', 'gasPrice')):
        raise ValueError('registration transaction quantities must be integers')
    if dict(plan) != expected:
        raise ValueError('registration plan or frozen transaction was changed')
    return expected, network, registry


def preflight(plan, *, rpc_factory=RpcClient):
    plan, network, registry = validate_plan(plan)
    adapter = ERC8004Client(network, registry, client_factory=rpc_factory)
    prepared = adapter.registration_transaction()
    tx = plan['transaction']
    if prepared != {key: (hex(tx[key]) if type(tx[key]) is int else tx[key])
        for key in ('from', 'to', 'chainId', 'value', 'data', 'gas')}:
        raise ValueError('registry adapter registration transaction mismatch')
    rows = []
    request = {key: (hex(value) if type(value) is int else value) for key, value in tx.items()}
    for url in network.rpc_urls:
        client = rpc_factory(url)
        latest = quantity(client.call('eth_getTransactionCount', [GAS_OWNER, 'latest']))
        pending = quantity(client.call('eth_getTransactionCount', [GAS_OWNER, 'pending']))
        price = quantity(client.call('eth_gasPrice', []))
        balance = quantity(client.call('eth_getBalance', [GAS_OWNER, 'latest']))
        estimate = quantity(client.call('eth_estimateGas', [request]))
        if latest != NONCE or pending != NONCE or price > tx['gasPrice'] or price == 0:
            raise ValueError('registration nonce/gas price changed')
        if estimate < 21_000 or estimate > tx['gas'] or balance < tx['gas'] * tx['gasPrice']:
            raise ValueError('registration funding or gas estimate is insufficient')
        rows.append((latest, pending, price, estimate))
    if rows[0] != rows[1]:
        raise ValueError('registration preflight RPCs disagree')
    return {'status': 'ready', 'signed': False, 'broadcast': False, 'plan_hash': plan['plan_hash']}


def _raw_matches(raw_hex, plan, tx_hash):
    try:
        raw = bytes.fromhex(raw_hex[2:]) if isinstance(raw_hex, str) and raw_hex.startswith('0x') else b''
        signed = Transaction.from_bytes(raw)
        if rlp.encode(signed) != raw or transaction_hash(tx_hash) != '0x' + keccak(raw).hex():
            raise ValueError('journal raw transaction/hash mismatch')
        values = signed.as_dict()
        tx = plan['transaction']
        expected = {key: tx[key] for key in ('nonce', 'gasPrice', 'gas', 'value')}
        expected.update(to=bytes.fromhex(tx['to'][2:]), data=bytes.fromhex(tx['data'][2:]))
        if (any(values[key] != value for key, value in expected.items())
            or values['v'] not in (CHAIN_ID * 2 + 35, CHAIN_ID * 2 + 36)
            or Account.recover_transaction(raw).lower() != GAS_OWNER):
            raise ValueError('journal signed transaction is outside the frozen plan')
    except Exception:
        raise ValueError('invalid private registration transaction journal') from None


def _read_journal(path, plan):
    if not path.exists(): return None
    value = load_configuration(path)
    if set(value) != {'schema_version', 'plan_hash', 'raw_transaction', 'transaction_hash', 'attempted'}:
        raise ValueError('registration journal shape changed')
    if value['schema_version'] != JOURNAL_SCHEMA or value['plan_hash'] != plan['plan_hash'] or type(value['attempted']) is not bool:
        raise ValueError('registration journal does not match plan')
    _raw_matches(value['raw_transaction'], plan, value['transaction_hash'])
    return value


def _public(plan, journal=None, *, status='validated', **extra):
    return dict(status=status, plan_hash=plan['plan_hash'], signed=journal is not None,
        broadcast=bool(journal and journal['attempted']),
        **({'transaction_hash': journal['transaction_hash']} if journal else {}), **extra)


def _event_identity(receipt, tx, plan):
    logs = receipt.get('logs')
    if not isinstance(logs, list): raise ValueError('registration receipt logs required')
    registered, transfers, indexes, events = [], [], set(), []
    owner_topic = '0x' + '00' * 12 + GAS_OWNER[2:]
    for log in logs:
        if not isinstance(log, Mapping): raise ValueError('registration log invalid')
        if address(log['address']) != IDENTITY: continue
        index = quantity(log['logIndex'])
        if (index in indexes or log.get('removed') is not False
            or transaction_hash(log['transactionHash']) != transaction_hash(tx['hash'])
            or transaction_hash(log['blockHash']) != transaction_hash(receipt['blockHash'])
            or quantity(log['blockNumber']) != quantity(receipt['blockNumber'])
            or quantity(log['transactionIndex']) != quantity(receipt['transactionIndex'])):
            raise ValueError('registration log is not canonical')
        indexes.add(index)
        topics = log.get('topics')
        if (not isinstance(topics, list) or not topics or any(
            not isinstance(topic, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', topic)
            for topic in topics)):
            raise ValueError('registration log topics invalid')
        if topics[0] == REGISTER_TOPIC:
            if len(topics) != 3 or topics[2].lower() != owner_topic:
                raise ValueError('registered owner mismatch')
            topic = topics[1]
            if not isinstance(topic, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', topic):
                raise ValueError('registered agent ID invalid')
            agent_id = int(topic, 16)
            try:
                data = log['data']
                if not isinstance(data, str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})*', data):
                    raise ValueError('invalid event data')
                raw = bytes.fromhex(data[2:])
                uri = decode(['string'], raw)[0]
                if encode(['string'], [uri]) != raw or uri != plan['registry']['agent_uri']:
                    raise ValueError('registered URI mismatch')
            except Exception:
                raise ValueError('registered URI ABI is invalid or does not match') from None
            registered.append(agent_id)
            events.append(('Registered', index, agent_id, uri))
        elif topics[0] == TRANSFER_TOPIC:
            if (len(topics) != 4 or topics[1].lower() != '0x' + '00' * 32
                or topics[2].lower() != owner_topic or log['data'] != '0x'
                or not isinstance(topics[3], str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', topics[3])):
                raise ValueError('registry mint transfer mismatch')
            transfers.append(int(topics[3], 16))
            events.append(('Transfer', index, transfers[-1]))
    if len(registered) != 1 or transfers != registered:
        raise ValueError('one matching Registered and mint Transfer required')
    return registered[0], events


def _reconcile(plan, journal, network, registry, rpc_factory):
    tx_hash, expected = journal['transaction_hash'], plan['transaction']
    # Reuse the canonical transaction proof. Its acquisition is deliberately
    # independent of the signer and of the operator's following event reads.
    proof = verify_wallet_transaction(network, owner=GAS_OWNER, tx_hash=tx_hash,
        to=IDENTITY, data=expected['data'])
    if proof is None: return _public(plan, journal, status='pending')
    clients = tuple(rpc_factory(url) for url in network.rpc_urls)
    adapter = ERC8004Client(network, registry, client_factory=rpc_factory)
    adapter._runtime_at(clients, proof['block_number'])
    rows = []
    for client in clients:
        if quantity(client.call('eth_chainId', [])) != CHAIN_ID:
            raise ValueError('registration evidence chain changed')
        tx = client.call('eth_getTransactionByHash', [tx_hash])
        receipt = client.call('eth_getTransactionReceipt', [tx_hash])
        if not isinstance(tx, Mapping) or not isinstance(receipt, Mapping):
            return _public(plan, journal, status='pending')
        observed_data = tx.get('input', tx.get('data'))
        if (observed_data != expected['data'] or ('data' in tx and tx['data'] != observed_data)
            or address(tx['from']) != GAS_OWNER or address(tx['to']) != IDENTITY
            or address(receipt['from']) != GAS_OWNER or address(receipt['to']) != IDENTITY
            or quantity(tx['value']) != 0 or quantity(tx['nonce']) != NONCE
            or quantity(tx['chainId']) != CHAIN_ID or quantity(tx.get('type', '0x0')) != 0
            or quantity(tx['gas']) != expected['gas'] or quantity(tx['gasPrice']) != expected['gasPrice']
            or quantity(receipt['status']) != 1 or quantity(receipt['gasUsed']) > expected['gas']
            or quantity(receipt['effectiveGasPrice']) != expected['gasPrice']
            or transaction_hash(tx['hash']) != tx_hash or transaction_hash(receipt['transactionHash']) != tx_hash
            or quantity(tx['blockNumber']) != proof['block_number'] or quantity(receipt['blockNumber']) != proof['block_number']
            or transaction_hash(tx['blockHash']) != proof['block_hash'] or transaction_hash(receipt['blockHash']) != proof['block_hash']
            or quantity(tx['transactionIndex']) != quantity(receipt['transactionIndex'])):
            raise ValueError('registration transaction evidence does not match frozen plan')
        agent_id, events = _event_identity(receipt, tx, plan)
        rows.append((agent_id, events, quantity(receipt['gasUsed']),
            quantity(receipt['effectiveGasPrice']), quantity(receipt['transactionIndex'])))
    if rows[0] != rows[1]: raise ValueError('registration event RPCs disagree')
    agent_id = rows[0][0]
    active = RegistryConfig.from_dict(plan['registry'] | {'agent_id': agent_id}, network=network, origin=ORIGIN)
    identity = ERC8004Client(network, active, client_factory=rpc_factory).verify_identity()
    if not identity or identity['block_number'] < proof['finality_block_number']:
        raise ValueError('current registry identity is not independently verified')
    # Anchor both the receipt and proof boundary again after all registry reads.
    adapter._assert_boundary_stable(clients, proof['block_number'], proof['block_hash'])
    adapter._assert_boundary_stable(clients, proof['finality_block_number'], proof['finality_block_hash'])
    return _public(plan, journal, status='complete', agent_id=agent_id, identity=identity,
        proof=proof, two_rpc_verified=True)


def run_registration(plan, journal_path, *, execute=False, signer_factory=None, rpc_factory=RpcClient):
    plan, network, registry = validate_plan(plan)
    path = Path(journal_path)
    try:
        with _journal_lock(path):
            journal = _read_journal(path, plan)
            if journal and journal['attempted']:
                try: return _reconcile(plan, journal, network, registry, rpc_factory)
                except (ValueError, RuntimeError, KeyError, TypeError):
                    return _public(plan, journal, status='blocked', error='Original registration evidence is unavailable or inconsistent; do not resend.')
            if not execute: return _public(plan, journal)
            preflight(plan, rpc_factory=rpc_factory)
            if journal is None:
                if signer_factory is None: raise ValueError('isolated operator signer required')
                signer = signer_factory()
                gas_signer = getattr(signer, 'gas_signer', None)
                if gas_signer is None: raise ValueError('isolated gas signer required')
                raw = sign_legacy_transaction(plan['transaction'], gas_signer.sign_digest,
                    expected_address=GAS_OWNER, chain_id=CHAIN_ID)
                journal = dict(schema_version=JOURNAL_SCHEMA, plan_hash=plan['plan_hash'],
                    raw_transaction='0x' + raw.hex(), transaction_hash='0x' + keccak(raw).hex(), attempted=False)
                _raw_matches(journal['raw_transaction'], plan, journal['transaction_hash'])
                _write_atomic(path, journal)
            # Preflight again after potentially slow KMS signing, before the
            # durable write-ahead marker. The exact signed bytes never change.
            preflight(plan, rpc_factory=rpc_factory)
            journal['attempted'] = True
            _write_atomic(path, journal)
            try:
                response = rpc_factory(RPC_URLS[0], writable=True).call('eth_sendRawTransaction', [journal['raw_transaction']])
                if transaction_hash(response) != journal['transaction_hash']: raise ValueError('broadcast hash mismatch')
            except Exception:
                return _public(plan, journal, status='pending')
            try: return _reconcile(plan, journal, network, registry, rpc_factory)
            except (ValueError, RuntimeError, KeyError, TypeError):
                return _public(plan, journal, status='blocked', error='Original registration evidence is unavailable or inconsistent; do not resend.')
    except OperatorError:
        raise ValueError('registration journal must be private and exclusively locked') from None


def _signer_factory(path, plan):
    # CLI only. No request parameters or ambient profile reach this boundary.
    from agentonomy_commerce.signer_process import _load_signer, validate_configuration
    configuration = load_configuration(path)
    scope = validate_configuration(configuration)
    _, network, registry = validate_plan(plan)
    expected = {'profile': 'agentonomy-commerce-monad-role', 'region': 'ap-southeast-1',
        'account_id': '793643674201', 'expected_role_arn': ROLE,
        'credential_directory': '/var/lib/agentonomy-sign/aws',
        'execution_key_arn': EXECUTION_KEY, 'gas_key_arn': GAS_KEY}
    if any(configuration[key] != value for key, value in expected.items()):
        raise ValueError('operator signer configuration is outside the approved role/key scope')
    if (scope.network != network or scope.relayer_address != GAS_OWNER
        or scope.owner != '0x59899831691aa79507818961773497c751bffc8b'
        or scope.execution_address != '0x968dbabb8dca19c4a8174a260cc40db66bdb7915'
        or scope.nonce_min != 5 or scope.nonce_max != 13):
        raise ValueError('operator signer nonce/address scope mismatch')
    _scope(scope.network, registry)
    def factory():
        with _sanitized_aws_environment(configuration):
            return _load_signer(configuration, scope)
    return factory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--network-config', required=True, type=Path)
    parser.add_argument('--registry-config', required=True, type=Path)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--rpc-check', action='store_true')
    parser.add_argument('--gas-price-wei', type=int)
    parser.add_argument('--journal', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--signer-config', type=Path)
    args = parser.parse_args(argv)
    try:
        network = validate_bootstrap(load_configuration(args.network_config)).network
        registry = RegistryConfig.from_dict(load_configuration(args.registry_config), network=network, origin=ORIGIN)
        if args.prepare:
            if args.execute or args.signer_config or args.journal: raise ValueError('prepare is read-only')
            plan = build_plan(network, registry, gas_price_wei=args.gas_price_wei)
            if args.rpc_check: preflight(plan)
            # Existing plans cannot be silently replaced, especially if a journal exists.
            with _journal_lock(args.plan):
                if args.plan.exists() or args.plan.is_symlink(): raise ValueError('plan already exists')
                _write_atomic(args.plan, plan)
            result = {'status': 'planned', 'signed': False, 'broadcast': False, 'plan_hash': plan['plan_hash']}
        else:
            if not args.journal or args.gas_price_wei is not None or args.rpc_check: raise ValueError('journal and frozen plan required')
            plan = load_configuration(args.plan)
            if plan['network'] != asdict(network) | {'rpc_urls': list(network.rpc_urls)} or plan['registry'] != asdict(registry):
                raise ValueError('plan/configuration changed')
            if args.execute and not args.signer_config: raise ValueError('explicit operator signer configuration required')
            if args.signer_config and not args.execute: raise ValueError('status cannot load signer')
            factory = _signer_factory(args.signer_config, plan) if args.execute else None
            result = run_registration(plan, args.journal, execute=args.execute, signer_factory=factory)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] in ('planned', 'validated', 'complete') else 2
    except Exception:
        print(json.dumps({'status': 'blocked',
            'error': 'Check private plan, isolated operator configuration and both RPCs. Preserve the original journal; do not resend.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
