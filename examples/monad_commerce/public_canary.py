"""Operator composition: external wallet setup, Core, real chain and delivery."""
from decimal import Decimal
import json
import os
from pathlib import Path

from eth_abi import encode
from eth_utils import keccak

from agentonomy_commerce.budget_network import NetworkConfig, RpcClient, quantity
from agentonomy_commerce.wallet_transactions import InvalidWalletTransaction, verify_wallet_transaction, transaction_hash
from examples.commerce.core_persistence import StateLock, _atomic_write_json
from examples.commerce.node import MarketplaceBridge
from examples.monad_commerce.public_bridge import PublicCoreBridge
from examples.monad_commerce.public_worker import validate_bootstrap
from scripts.monad.preflight import inspect_configuration


class PublicCanary:
    def __init__(self, state_dir, configuration):
        if not isinstance(configuration, dict) or set(configuration) != {'deployment', 'signer_configuration'}:
            raise ValueError('deployment manifest required')
        self.manifest = configuration['deployment']
        self.bootstrap = {'deployment': {k: v for k, v in self.manifest.items()
                                         if k not in {'token_code_hash', 'executor_code_hash'}},
                          'signer_configuration': configuration['signer_configuration']}
        scope = validate_bootstrap(self.bootstrap)
        self.network, self.owner = scope.network, scope.owner
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.core = self.market = self.lock = None
        self.wallet_operations = {}

    def __enter__(self):
        try:
            report = inspect_configuration(self.manifest, rpc_check=True)
            if report['status'] != 'deployed_configuration_verified':
                raise ValueError('deployed code hash verification required')
            for url in self.network.rpc_urls:
                rpc = RpcClient(url)
                rpc.check_chain(self.network.chain_id)
                if rpc.call('eth_getCode', [self.owner, 'latest']) != '0x':
                    raise ValueError('first canary requires a plain EOA owner')
            self.state_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.state_dir, 0o700)
            operator_dir = self.state_dir / 'operator'
            operator_dir.mkdir(mode=0o700, exist_ok=True)
            self.lock = StateLock(operator_dir)
            self.lock.acquire()
            self.operations_path = operator_dir / 'wallet-operations.json'
            if self.operations_path.exists():
                self.wallet_operations = json.loads(self.operations_path.read_text())
            self.core = PublicCoreBridge(self.state_dir, self.bootstrap)
            self.core.__enter__()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.core:
            self.core.close()
            self.core = None
        if self.market:
            self.market.close()
            self.market = None
        if self.lock:
            self.lock.release()
            self.lock = None

    def _onboarding_status(self):
        if self.market:
            return self.market.request('onboarding_status')
        return self.core.onboarding('onboarding_status')

    def status(self):
        onboarding = self._onboarding_status()
        commerce = self.market.request('snapshot') if self.market else None
        return {'owner': self.owner, 'chain_id': self.network.chain_id,
                'token': self.network.token, 'executor': self.network.executor,
                'payee': self.network.payee, 'mode': self.network.mode,
                'token_symbol': 'TestUSD', 'simulation': False, 'real_funds': False,
                'terms': {'total': '1.00', 'per_payment': '0.50', 'price': '0.30'},
                'onboarding': onboarding, 'commerce': commerce,
                'wallet_operations': self.wallet_operations}

    def onboarding(self, operation, **params):
        if self.market:
            if operation == 'onboarding_status':
                return self._onboarding_status()
            raise ValueError('wallet is already onboarded; no replacement grant is issued')
        return self.core.onboarding(operation, **params)

    def _start_market(self):
        if self.market:
            return
        state = self._onboarding_status()
        if not all(state.get(key) for key in ('wallet_identity_id', 'spending_grant_id', 'budget_binding_id', 'allowance_id')):
            raise ValueError('wallet setup must be completed first')
        self.core.close()
        self.core = None
        self.market = MarketplaceBridge(
            self.state_dir,
            worker_module='examples.monad_commerce.public_market_worker',
            timeout_seconds=240,
        )
        try:
            self.market.request('initialize', self.bootstrap)
        except BaseException:
            self.market.close()
            self.market = None
            # Do not restart implicitly after an unknown worker outcome.
            raise

    def request(self, method, args=None):
        if method not in {'search', 'details', 'preview', 'execute', 'purchase', 'recover_purchase', 'snapshot'}:
            raise ValueError('operation outside commerce scope')
        self._start_market()
        return self.market.request(method, args)

    def _wallet_transaction(self, to, data):
        prices = []
        for url in self.network.rpc_urls:
            rpc = RpcClient(url)
            rpc.check_chain(self.network.chain_id)
            prices.append(quantity(rpc.call('eth_gasPrice', [])))
        price = max(prices)
        if not 0 < price <= self.network.max_gas_price_wei:
            raise ValueError('wallet gas price exceeds configured cap')
        return {'from': self.owner, 'to': to, 'value': '0x0', 'data': data,
                'chainId': hex(self.network.chain_id), 'gas': hex(100000), 'gasPrice': hex(price)}

    def _approval_data(self):
        return '0x' + (keccak(text='approve(address,uint256)')[:4]
                       + encode(['address', 'uint256'], [self.network.executor, 1000000])).hex()

    def approval_transaction(self):
        if self._onboarding_status()['phase'] != 'allowance':
            raise ValueError('complete the signed budget grant before allowance')
        if self.wallet_operations.get('approval', {}).get('transaction_hash'):
            raise ValueError('an approval was already submitted; verify the existing hash')
        return {'transaction': self._wallet_transaction(self.network.token, self._approval_data()),
                'amount_atomic': '1000000', 'token_symbol': 'TestUSD'}

    def _record_hash(self, operation, tx_hash):
        tx_hash = transaction_hash(tx_hash)
        record = self.wallet_operations.setdefault(operation, {})
        if record.get('transaction_hash') not in (None, tx_hash):
            raise ValueError('existing wallet transaction requires inspection; do not replace it')
        if not record.get('transaction_hash'):
            record['status'] = 'pending'
        record['transaction_hash'] = tx_hash
        _atomic_write_json(self.operations_path, self.wallet_operations)
        return record

    def verify_approval(self, tx_hash):
        record = self._record_hash('approval', tx_hash)
        if record.get('verified'):
            return record
        evidence = self._verify_wallet_candidate('approval', record,
            to=self.network.token, data=self._approval_data())
        if evidence is None:
            return {'verified': False, 'status': 'pending', 'transaction_hash': tx_hash}
        result = self.onboarding('allowance_verify', transaction_hash=tx_hash)
        record.update(evidence, status='verified')
        _atomic_write_json(self.operations_path, self.wallet_operations)
        return record | {'onboarding': result}

    def _verify_wallet_candidate(self, operation, record, *, to, data):
        try:
            return verify_wallet_transaction(self.network, owner=self.owner,
                tx_hash=record['transaction_hash'], to=to, data=data)
        except InvalidWalletTransaction:
            # Unknown/pending or disagreeing RPC results retain the original hash.
            # Only two agreeing canonical observations can permit explicit correction.
            rejected = dict(record)
            rejected['rejected_transaction_hash'] = rejected.pop('transaction_hash')
            rejected['status'] = 'rejected'
            operations = dict(self.wallet_operations, **{operation: rejected})
            _atomic_write_json(self.operations_path, operations)
            self.wallet_operations = operations
            raise

    def revoke_prepare(self):
        status = self._onboarding_status()
        grant_id = transaction_hash(status['chain_grant_id'])
        if self.market:
            self.market.request('revoke')
        else:
            self.core.revoke()
        record = self.wallet_operations.setdefault('revocation', {})
        record.update(core_revoked=True, chain_grant_id=grant_id)
        _atomic_write_json(self.operations_path, self.wallet_operations)
        data = '0x' + (keccak(text='revoke(bytes32)')[:4] + bytes.fromhex(grant_id[2:])).hex()
        if record.get('transaction_hash'):
            return record | {'chain_revoked': record.get('verified', False)}
        return record | {'chain_revoked': False, 'transaction': self._wallet_transaction(self.network.executor, data)}

    def verify_revocation(self, tx_hash):
        record = self.wallet_operations.get('revocation')
        if not record or not record.get('core_revoked'):
            raise ValueError('prepare revocation before verifying it')
        record = self._record_hash('revocation', tx_hash)
        if record.get('verified'):
            return record
        data = '0x' + (keccak(text='revoke(bytes32)')[:4] + bytes.fromhex(record['chain_grant_id'][2:])).hex()
        evidence = self._verify_wallet_candidate('revocation', record,
            to=self.network.executor, data=data)
        if evidence is None:
            return {'core_revoked': True, 'chain_revoked': False, 'status': 'pending', 'transaction_hash': tx_hash}
        record.update(evidence, status='verified', chain_revoked=True)
        _atomic_write_json(self.operations_path, self.wallet_operations)
        return record
