"""Purchase-only capability enforced inside the credential-owning signer.

This additional boundary does not replace Core policy/reservation checks or the
contract's cumulative accounting. The signing worker has no broadcast capability.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from eth_abi import decode
from agentonomy_commerce.budget_network import NetworkConfig, address
from apps.facilitator.budget_protocol import (
    SpendGrant, PurchaseExecution, EXECUTE_SELECTOR, hash_grant, hash_execution,
    verify_grant_signature, verify_execution_signature, encode_execute_calldata_hex,
)


@dataclass(frozen=True)
class SigningScope:
    network: NetworkConfig
    owner: str
    execution_address: str
    relayer_address: str
    nonce_min: int
    nonce_max: int

    def __post_init__(self):
        for field in ('owner', 'execution_address', 'relayer_address'):
            object.__setattr__(self, field, address(getattr(self, field)))
        distinct = {self.owner, self.execution_address, self.relayer_address,
                    self.network.token, self.network.executor}
        if len(distinct) != 5 or self.network.payee in distinct - {self.relayer_address}:
            raise ValueError('buyer, signers and contracts must be distinct; merchant may use relayer')
        if (type(self.nonce_min) is not int or type(self.nonce_max) is not int
            or not 0 <= self.nonce_min <= self.nonce_max < 2**64
            or self.nonce_max - self.nonce_min > 8):
            raise ValueError('bounded nonce range required')

    @classmethod
    def from_json(cls, value):
        expected = {'network', 'owner', 'execution_address', 'relayer_address', 'nonce_min', 'nonce_max'}
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError('invalid signing scope')
        return cls(**(value | {'network': NetworkConfig(**value['network'])}))


class ScopedSigner:
    def __init__(self, scope, execution_signer, gas_signer):
        self.scope = scope
        self.execution_signer = execution_signer
        self.gas_signer = gas_signer

    def _validate_execution(self, grant, owner_signature, execution):
        if not isinstance(grant, SpendGrant):
            grant = SpendGrant.from_json(grant)
        if not isinstance(execution, PurchaseExecution):
            execution = PurchaseExecution.from_json(execution)
        scope, network = self.scope, self.scope.network
        if (grant.owner, grant.token, grant.payee, grant.execution_signer) != (
            scope.owner, network.token, network.payee, scope.execution_address
        ):
            raise ValueError('grant differs from signing scope')
        if (grant.max_per_payment > 500000 or grant.max_total > 1000000
            or grant.valid_until - grant.valid_after > 86460):
            raise ValueError('grant exceeds canary limits')
        now = int(time.time())
        if not grant.valid_after <= now < execution.deadline <= min(now + 120, grant.valid_until):
            raise ValueError('execution outside authorized time window')
        if execution.amount != 300000 or execution.amount > grant.max_per_payment:
            raise ValueError('execution differs from fixed service price')
        if execution.grant_hash != '0x' + hash_grant(grant, network.chain_id, network.executor).hex():
            raise ValueError('execution grant commitment mismatch')
        verify_grant_signature(grant, owner_signature, network.chain_id, network.executor)
        return grant, execution

    def sign_execution(self, grant, owner_signature, execution):
        _, execution = self._validate_execution(grant, owner_signature, execution)
        network = self.scope.network
        signature = self.execution_signer.sign_digest(hash_execution(execution, network.chain_id, network.executor))
        verify_execution_signature(execution, signature, self.scope.execution_address, network.chain_id, network.executor)
        return signature

    def sign_transaction(self, transaction):
        from agentonomy_commerce.kms_adapter import sign_legacy_transaction
        scope, network = self.scope, self.scope.network
        fields = {'chainId', 'nonce', 'to', 'value', 'data', 'gasPrice', 'gas', 'from'}
        if not isinstance(transaction, dict) or set(transaction) != fields:
            raise ValueError('unexpected transaction fields')
        if (transaction['chainId'] != network.chain_id or type(transaction['chainId']) is not int
            or address(transaction['to']) != network.executor
            or address(transaction['from']) != scope.relayer_address
            or type(transaction['value']) is not int or transaction['value'] != 0):
            raise ValueError('transaction differs from signing scope')
        for name, minimum, maximum in (
            ('nonce', scope.nonce_min, scope.nonce_max),
            ('gas', 21000, network.gas_limit),
            ('gasPrice', 1, network.max_gas_price_wei),
        ):
            if type(transaction[name]) is not int or not minimum <= transaction[name] <= maximum:
                raise ValueError('transaction exceeds signing limits')
        data = transaction['data']
        if not isinstance(data, str) or not data.startswith('0x') or len(data) > 8192:
            raise ValueError('invalid purchase calldata')
        try:
            raw = bytes.fromhex(data[2:])
            if raw[:4] != EXECUTE_SELECTOR:
                raise ValueError('wrong selector')
            decoded_grant, owner_signature, decoded_execution, execution_signature = decode(
                ['(bytes32,address,bytes32,address,address,uint256,uint256,uint256,uint256,address)',
                 'bytes', '(bytes32,bytes32,bytes32,uint256,uint256)', 'bytes'], raw[4:])
            grant = SpendGrant(*decoded_grant)
            execution = PurchaseExecution(*decoded_execution)
        except Exception:
            raise ValueError('invalid purchase calldata') from None
        self._validate_execution(grant, owner_signature, execution)
        verify_execution_signature(execution, execution_signature, scope.execution_address,
                                   network.chain_id, network.executor)
        canonical = encode_execute_calldata_hex(grant, owner_signature, execution, execution_signature)
        if data != canonical:
            raise ValueError('non-canonical purchase calldata')
        return sign_legacy_transaction(transaction, self.gas_signer.sign_digest,
                                       expected_address=scope.relayer_address, chain_id=network.chain_id)
