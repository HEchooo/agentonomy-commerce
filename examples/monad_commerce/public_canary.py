"""Operator composition: external wallet setup, Core, real chain and delivery."""
from copy import deepcopy
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
from agentonomy_commerce.service_pricing import atomic_price_terms


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
        self._service_payment_terms = atomic_price_terms(
            getattr(scope, "service_amount_atomic", 300000)
        )
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
        service_payment_terms = getattr(
            self, "_service_payment_terms", atomic_price_terms(300000)
        )
        return {'owner': self.owner, 'chain_id': self.network.chain_id,
                'token': self.network.token, 'executor': self.network.executor,
                'payee': self.network.payee, 'mode': self.network.mode,
                'token_symbol': 'TestUSD', 'simulation': False, 'real_funds': False,
                'terms': {'total': '1.00', 'per_payment': '0.50',
                          'price': service_payment_terms['price_usd']},
                'onboarding': onboarding, 'commerce': commerce,
                'wallet_operations': self.wallet_operations}

    def onboarding(self, operation, **params):
        if self.market:
            if operation == 'onboarding_status':
                return self._onboarding_status()
            raise ValueError('wallet is already onboarded; no replacement grant is issued')
        old_grant_id = None
        if operation == 'grant_renew_verify':
            old_grant_id = (self._onboarding_status() or {}).get('spending_grant_id')
        result = self.core.onboarding(operation, **params)
        if operation == 'grant_renew_verify' and isinstance(result, dict):
            self._archive_grant_generation(old_grant_id, result.get('spending_grant_id'))
        return result

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

    def _persist_wallet_operations(self):
        """Persist the operator journal without dropping prior generations."""
        _atomic_write_json(self.operations_path, self.wallet_operations)

    def _operation_scope(self, operation, grant_id=None):
        """Return the wallet operation record for the current Core grant.

        The original journal used one top-level record for approval and
        revocation. A renewed Core grant must never inherit that record's
        transaction hash or chain grant id. When a scoped record changes, the
        prior record is copied into an append-only history map before a fresh
        record is selected. An existing unscoped transaction is deliberately
        refused: its grant generation cannot be proven from the journal.
        """
        if operation not in {'approval', 'revocation'}:
            return self.wallet_operations.setdefault(operation, {})
        if grant_id is None:
            try:
                grant_id = (self._onboarding_status() or {}).get('spending_grant_id')
            except (AttributeError, KeyError, TypeError):
                grant_id = None
        record = self.wallet_operations.setdefault(operation, {})
        if not isinstance(record, dict):
            raise ValueError(f'{operation} wallet operation journal is invalid')
        if not grant_id:
            return record

        scoped_id = record.get('spending_grant_id')
        if scoped_id is None:
            # Empty legacy records can be bound to the observed current grant.
            # A transaction-bearing legacy record is unsafe to reuse after a
            # renewal because its generation cannot be established.
            if any(record.get(key) for key in (
                'transaction_hash', 'rejected_transaction_hash', 'verified',
                'status', 'core_revoked', 'chain_revoked',
            )):
                raise ValueError(
                    f'{operation} wallet operation is unscoped; inspect it before reuse'
                )
            record['spending_grant_id'] = grant_id
            self._persist_wallet_operations()
            return record
        if scoped_id == grant_id:
            return record

        history_key = operation + '_history'
        history = self.wallet_operations.setdefault(history_key, {})
        if not isinstance(history, dict):
            raise ValueError(f'{history_key} wallet operation journal is invalid')
        archived = history.get(scoped_id)
        if archived is not None and archived != record:
            raise ValueError(f'{history_key} has conflicting evidence for the grant generation')
        history.setdefault(scoped_id, deepcopy(record))
        current = history.get(grant_id)
        if current is None:
            current = {'spending_grant_id': grant_id}
        elif not isinstance(current, dict):
            raise ValueError(f'{history_key} contains an invalid grant generation')
        else:
            current = deepcopy(current)
            current['spending_grant_id'] = grant_id
        self.wallet_operations[operation] = current
        self._persist_wallet_operations()
        return current

    def _archive_grant_generation(self, old_grant_id, new_grant_id):
        """Archive wallet operations when Core promotes a new grant.

        This is called after a successful renewal and is also safe to replay
        after a lost response. Existing pending/unknown records stay intact in
        history; only the current-generation pointer advances.
        """
        if not old_grant_id or not new_grant_id or old_grant_id == new_grant_id:
            return
        changed = False
        for operation in ('approval', 'revocation'):
            record = self.wallet_operations.setdefault(operation, {})
            if not isinstance(record, dict):
                raise ValueError(f'{operation} wallet operation journal is invalid')
            scoped_id = record.get('spending_grant_id')
            archive_under_old_generation = True
            if scoped_id is None:
                if record:
                    migrated = False
                    if operation == 'approval' and record.get('transaction_hash'):
                        # A legacy journal has no generation marker. Migrate
                        # it only after an independent two-RPC transaction
                        # observation proves it is the finite approval this
                        # wallet flow expects. A pending or invalid legacy
                        # hash is quarantined as unscoped evidence instead of
                        # being guessed into the new grant generation.
                        try:
                            evidence = verify_wallet_transaction(
                                self.network,
                                owner=self.owner,
                                tx_hash=record['transaction_hash'],
                                to=self.network.token,
                                data=self._approval_data(),
                            )
                        except Exception:
                            evidence = None
                        if evidence is not None:
                            record = deepcopy(record)
                            record.update(evidence, status='verified', verified=True)
                            record['spending_grant_id'] = old_grant_id
                            migrated = True
                    if not migrated:
                        unscoped_key = 'unscoped'
                        if record.get('transaction_hash'):
                            unscoped_key += ':' + str(record['transaction_hash']).lower()
                        history_key = operation + '_history'
                        history = self.wallet_operations.setdefault(history_key, {})
                        if not isinstance(history, dict):
                            raise ValueError(f'{history_key} wallet operation journal is invalid')
                        archived = history.get(unscoped_key)
                        if archived is not None and archived != record:
                            raise ValueError(f'{history_key} has conflicting unscoped evidence')
                        history.setdefault(unscoped_key, deepcopy(record))
                        record = {}
                        archive_under_old_generation = False
            elif scoped_id != old_grant_id:
                # A journal already on a newer generation must not be replaced
                # by a stale renewal response.
                continue
            history_key = operation + '_history'
            history = self.wallet_operations.setdefault(history_key, {})
            if not isinstance(history, dict):
                raise ValueError(f'{history_key} wallet operation journal is invalid')
            if archive_under_old_generation:
                archived = history.get(old_grant_id)
                if archived is not None and archived != record:
                    raise ValueError(f'{history_key} has conflicting evidence for the grant generation')
                history.setdefault(old_grant_id, deepcopy(record))
            current = history.get(new_grant_id)
            self.wallet_operations[operation] = (
                deepcopy(current) if isinstance(current, dict)
                else {'spending_grant_id': new_grant_id}
            )
            self.wallet_operations[operation]['spending_grant_id'] = new_grant_id
            changed = True
        if changed:
            self._persist_wallet_operations()

    def approval_transaction(self):
        status = self._onboarding_status()
        if status['phase'] != 'allowance':
            raise ValueError('complete the signed budget grant before allowance')
        record = self._operation_scope('approval', status.get('spending_grant_id'))
        if record.get('transaction_hash'):
            raise ValueError('an approval was already submitted; verify the existing hash')
        return {'transaction': self._wallet_transaction(self.network.token, self._approval_data()),
                'amount_atomic': '1000000', 'token_symbol': 'TestUSD'}

    def _record_hash(self, operation, tx_hash, grant_id=None):
        tx_hash = transaction_hash(tx_hash)
        record = self._operation_scope(operation, grant_id)
        if record.get('transaction_hash') not in (None, tx_hash):
            raise ValueError('existing wallet transaction requires inspection; do not replace it')
        if not record.get('transaction_hash'):
            record['status'] = 'pending'
        record['transaction_hash'] = tx_hash
        self._persist_wallet_operations()
        return record

    def verify_approval(self, tx_hash):
        status = self._onboarding_status()
        record = self._record_hash('approval', tx_hash, status.get('spending_grant_id'))
        if record.get('verified'):
            return record
        evidence = self._verify_wallet_candidate('approval', record,
            to=self.network.token, data=self._approval_data())
        if evidence is None:
            return {'verified': False, 'status': 'pending', 'transaction_hash': tx_hash}
        result = self.onboarding('allowance_verify', transaction_hash=tx_hash)
        record.update(evidence, status='verified')
        self._persist_wallet_operations()
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
        spending_grant_id = status.get('spending_grant_id')
        chain_grant_id = transaction_hash(status['chain_grant_id'])
        record = self._operation_scope('revocation', spending_grant_id)
        if self.market:
            self.market.request('revoke')
        else:
            self.core.revoke()
        record.update(core_revoked=True, chain_grant_id=chain_grant_id)
        self._persist_wallet_operations()
        data = '0x' + (keccak(text='revoke(bytes32)')[:4] + bytes.fromhex(chain_grant_id[2:])).hex()
        if record.get('transaction_hash'):
            return record | {'chain_revoked': record.get('verified', False)}
        return record | {'chain_revoked': False, 'transaction': self._wallet_transaction(self.network.executor, data)}

    def verify_revocation(self, tx_hash):
        status = self._onboarding_status()
        spending_grant_id = status.get('spending_grant_id')
        record = self._operation_scope('revocation', spending_grant_id)
        if not record or not record.get('core_revoked'):
            raise ValueError('prepare revocation before verifying it')
        expected_chain_grant_id = status.get('chain_grant_id')
        if expected_chain_grant_id and record.get('chain_grant_id'):
            if transaction_hash(record['chain_grant_id']) != transaction_hash(expected_chain_grant_id):
                raise ValueError('revocation wallet operation belongs to another grant generation')
        record = self._record_hash('revocation', tx_hash, spending_grant_id)
        if record.get('verified'):
            return record
        data = '0x' + (keccak(text='revoke(bytes32)')[:4] + bytes.fromhex(record['chain_grant_id'][2:])).hex()
        evidence = self._verify_wallet_candidate('revocation', record,
            to=self.network.executor, data=data)
        if evidence is None:
            return {'core_revoked': True, 'chain_revoked': False, 'status': 'pending', 'transaction_hash': tx_hash}
        record.update(evidence, status='verified', chain_revoked=True)
        self._persist_wallet_operations()
        return record
