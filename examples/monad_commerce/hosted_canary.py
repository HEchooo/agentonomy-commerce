"""Wallet-scoped composition sharing one trusted Monad relayer admission gate."""
from datetime import UTC, datetime
import json
import os
import re

from eth_abi import decode
from eth_utils import keccak

from agentonomy_commerce.budget_network import RpcClient
from agentonomy_commerce.wallet_transactions import verify_wallet_transaction
from examples.commerce.node import MarketplaceBridge
from examples.commerce.core_persistence import _atomic_write_json
from examples.commerce.core_persistence import StateLock
from examples.monad_commerce.public_canary import PublicCanary
from examples.monad_commerce.hosted_process import HostedCoreBridge
from scripts.monad.preflight import inspect_configuration


class HostedCanary(PublicCanary):
    @property
    def broken(self):
        for bridge in (self.core, self.market):
            if bridge is not None:
                if getattr(bridge, 'broken', False) or getattr(bridge, '_broken', None) is not None:
                    return True
                process = getattr(bridge, 'process', None)
                if process is not None and process.poll() is not None:
                    return True
        return self.core is None and self.market is None

    def __init__(self, state_dir, configuration, *, public_origin, tenant_id, gate):
        super().__init__(state_dir, configuration)
        if gate.network != self.network or gate.relayer != configuration['deployment']['relayer'].lower():
            raise ValueError('all wallets must share the pinned relayer gate')
        self.public_origin = public_origin
        self.tenant_id, self.gate = tenant_id, gate

    def __enter__(self):
        try:
            report = inspect_configuration(self.manifest, rpc_check=True)
            if report['status'] != 'deployed_configuration_verified':
                raise ValueError('deployed code hash verification required')
            for url in self.network.rpc_urls:
                rpc = RpcClient(url)
                rpc.check_chain(self.network.chain_id)
                if rpc.call('eth_getCode', [self.owner, 'latest']) != '0x':
                    raise ValueError('this wallet flow requires a plain EOA owner')
            from examples.monad_commerce.hosted_service import _private_directory
            _private_directory(self.state_dir)
            operator_dir = self.state_dir / 'operator'
            _private_directory(operator_dir)
            self.lock = StateLock(operator_dir)
            self.lock.acquire()
            self.operations_path = operator_dir / 'wallet-operations.json'
            if self.operations_path.exists() or self.operations_path.is_symlink():
                import stat
                info = self.operations_path.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
                    raise ValueError('unsafe wallet operation journal')
                self.wallet_operations = json.loads(self.operations_path.read_text())
            self.bootstrap['public_origin'] = self.public_origin
            self.core = HostedCoreBridge(self.state_dir, self.bootstrap)
            self.core.__enter__()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def hosted(self, method, **params):
        if self.market:
            return self.market.request('hosted', {'method': method, 'params': params})
        return self.core.hosted(method, **params)

    def _start_market(self):
        if self.market:
            return
        state = self._onboarding_status()
        if not all(state.get(key) for key in ('wallet_identity_id', 'spending_grant_id', 'budget_binding_id', 'allowance_id')):
            raise ValueError('wallet authorization required')
        self.core.close()
        self.core = None
        self.market = MarketplaceBridge(
            self.state_dir, worker_module='examples.monad_commerce.hosted_process',
            worker_args=('market',), timeout_seconds=240,
        )
        try:
            self.market.request('initialize', self.bootstrap)
        except BaseException:
            self.market.close()
            self.market = None
            raise

    def _verified_gate_proof(self, result, purchase_id):
        evidence = result.get('settlement') or {}
        if result.get('purchase_id') != purchase_id:
            raise ValueError('payment result belongs to another order')
        if not evidence.get('verified'):
            return None
        if (evidence.get('status') != 'verified'
            or evidence.get('verified') is not True
            or evidence.get('two_rpc_verified') is not True
            or type(evidence.get('chain_id')) is not int
            or evidence.get('chain_id') != 10143
            or type(evidence.get('receipt_status')) is not int
            or evidence.get('receipt_status') != 1
            or not isinstance(evidence.get('transaction_hash'), str)
            or re.fullmatch(r'0x[0-9a-f]{64}', evidence['transaction_hash']) is None
            or evidence.get('token') != self.network.token
            or evidence.get('payee') != self.network.payee
            or evidence.get('executor') != self.network.executor
            or evidence.get('owner') != self.owner
            or str(evidence.get('amount_atomic')) != '300000'
            or not evidence.get('purchase_commitment')
            or not evidence.get('quote_commitment')
            or not evidence.get('grant_hash')
            or not evidence.get('block_hash')
            or not evidence.get('finality_block_hash')):
            raise ValueError('canonical Core payment evidence is incomplete')
        return {
            'tenant_id': self.tenant_id, 'purchase_id': purchase_id, 'owner': self.owner,
            'verified': True, 'two_rpc_verified': evidence.get('two_rpc_verified'),
            'chain_id': evidence.get('chain_id'), 'receipt_status': evidence.get('receipt_status'),
            'transaction_hash': evidence.get('transaction_hash'),
            'amount_atomic': str(evidence.get('amount_atomic')),
            'token': evidence.get('token'), 'payee': evidence.get('payee'),
        }

    def request(self, method, args=None):
        args = args or {}
        if method not in {'execute', 'recover_purchase'}:
            return super().request(method, args)
        self._start_market()
        if method == 'execute':
            preview_id = args.get('preview_id')
            preview = self.market.request('preview_state', {'preview_id': preview_id})
            if preview.get('preview_id') != preview_id:
                raise ValueError('preview scope mismatch')
            purchase_id = 'purchase_' + preview_id.removeprefix('preview_')
        else:
            purchase_id = args.get('purchase_id')
            existing = self.market.request('purchase', {'purchase_id': purchase_id})
            preview = self.market.request('preview_state', {'preview_id': existing['preview_id']})
        # A previously verified order remains a query/delivery recovery. Core's
        # existing idempotency and paid-input checks own its replay protection.
        try:
            existing = self.market.request('purchase', {'purchase_id': purchase_id})
        except RuntimeError as exc:
            if str(exc) != 'purchase_not_found':
                raise
            existing = None
        completed_proof = self._verified_gate_proof(existing, purchase_id) if existing is not None else None
        if completed_proof:
            self.gate.reconcile_verified_payment(completed_proof)
            return self.market.request(method, args)
        if existing is None:
            expires = datetime.fromisoformat(preview['expires_at'].replace('Z', '+00:00'))
            if expires <= datetime.now(UTC):
                raise ValueError('preview expired before payment admission')
        with self.gate.enter(self.tenant_id, purchase_id, self.owner) as lease:
            result = self.market.request(method, args)
            proof = self._verified_gate_proof(result, purchase_id)
            if proof:
                lease.complete_verified_payment(proof)
            elif result.get('state') not in {'payment_submitted', 'paid_but_undelivered'}:
                safety = self.hosted('payment_safety', purchase_id=purchase_id)
                if safety.get('no_broadcast') is True:
                    lease.abort_unsubmitted(safety | {'tenant_id': self.tenant_id})
            return result

    def claim_transaction(self):
        claimed = []
        for url in self.network.rpc_urls:
            rpc = RpcClient(url)
            rpc.check_chain(self.network.chain_id)
            data = '0x' + (keccak(text='claimed(address)')[:4] + bytes.fromhex(self.owner[2:]).rjust(32, b'\0')).hex()
            raw = rpc.call('eth_call', [{'to': self.network.token, 'data': data}, 'latest'])
            claimed.append(decode(['bool'], bytes.fromhex(raw[2:]))[0])
        if claimed != [False, False]:
            raise ValueError('claim already used or RPC disagreement')
        if self.wallet_operations.get('claim', {}).get('transaction_hash'):
            raise ValueError('verify the existing claim transaction')
        return {'transaction': self._wallet_transaction(self.network.token, '0x4e71d92d'),
                'amount_atomic': '1000000', 'token_symbol': 'TestUSD'}

    def verify_claim(self, tx_hash):
        record = self._record_hash('claim', tx_hash)
        if record.get('verified'):
            return record
        evidence = self._verify_wallet_candidate('claim', record, to=self.network.token, data='0x4e71d92d')
        if evidence is None:
            return {'verified': False, 'status': 'pending', 'transaction_hash': tx_hash}
        record.update(evidence, status='verified')
        _atomic_write_json(self.operations_path, self.wallet_operations)
        return record
