from dataclasses import replace
from types import SimpleNamespace
import time

import pytest
from eth_account import Account
from eth_keys import keys
from eth_utils import keccak

from agentonomy_commerce.budget_backend import BudgetBackend
from agentonomy_commerce.budget_network import NetworkConfig
from agentonomy_commerce.kms_scope import SigningScope, ScopedSigner
from apps.facilitator.budget_protocol import (
    SpendGrant, PurchaseExecution, hash_grant, hash_execution, sign_grant,
    encode_execute_calldata_hex,
)


class LocalDigestSigner:
    def __init__(self, key):
        self.key = key
        self.calls = []

    def sign_digest(self, digest):
        self.calls.append(digest)
        signature = keys.PrivateKey(bytes(self.key)).sign_msg_hash(digest)
        return signature.r.to_bytes(32, 'big') + signature.s.to_bytes(32, 'big') + bytes([signature.v + 27])


@pytest.fixture
def setup_scope():
    owner, execution_key, gas = [Account.from_key(bytes([i]) * 32) for i in (21, 22, 23)]
    network = NetworkConfig('monad_testnet', 10143,
        ('https://testnet-rpc.monad.xyz', 'https://rpc-testnet.monadinfra.com'),
        '0x' + '11' * 20, '0x' + '22' * 20, '0x' + '33' * 20)
    scope = SigningScope(network, owner.address, execution_key.address, gas.address, 2, 5)
    execution = LocalDigestSigner(execution_key.key)
    relayer = LocalDigestSigner(gas.key)
    signer = ScopedSigner(scope, execution, relayer)
    now = int(time.time())
    grant = SpendGrant('0x' + 'aa' * 32, owner.address, '0x' + 'bb' * 32,
        network.token, network.payee, 500000, 1000000, now - 60, now + 3600, execution_key.address)
    signature = sign_grant(grant, owner.key, 10143, network.executor)
    purchase = PurchaseExecution('0x' + hash_grant(grant, 10143, network.executor).hex(),
        '0x' + 'cc' * 32, '0x' + 'dd' * 32, 300000, now + 90)
    return SimpleNamespace(**locals())


def transaction(s, execution_signature):
    return dict(chainId=10143, nonce=2, to=s.network.executor, value=0,
        data=encode_execute_calldata_hex(s.grant, s.signature, s.purchase, execution_signature),
        gas=200000, gasPrice=1000000000, **{'from': s.gas.address})


def test_scoped_signer_verifies_owner_and_gas_transaction(setup_scope):
    s = setup_scope
    signature = s.signer.sign_execution(s.grant, s.signature, s.purchase)
    assert s.execution.calls == [hash_execution(s.purchase, 10143, s.network.executor)]
    tx = transaction(s, signature)
    raw = s.signer.sign_transaction(tx)
    assert Account.recover_transaction(raw).lower() == s.gas.address.lower()
    assert len(s.relayer.calls) == 1


def test_merchant_can_receive_test_token_at_project_gas_address(setup_scope):
    s = setup_scope
    scope = replace(s.scope, network=replace(s.network, payee=s.gas.address))
    assert scope.network.payee == scope.relayer_address


@pytest.mark.parametrize('change', [
    {'owner': '0x' + '99' * 20}, {'payee': '0x' + '99' * 20},
    {'token': '0x' + '99' * 20}, {'execution_signer': '0x' + '99' * 20},
    {'max_total': 1000001}, {'max_per_payment': 500001},
    {'valid_until': 9999999999},
])
def test_execution_scope_tampering_never_calls_kms(setup_scope, change):
    s = setup_scope
    with pytest.raises(ValueError):
        s.signer.sign_execution(replace(s.grant, **change), s.signature, s.purchase)
    assert not s.execution.calls


@pytest.mark.parametrize('change', [
    {'amount': 299999}, {'amount': 300001}, {'grant_hash': '0x' + 'ee' * 32},
    {'deadline': 1}, {'deadline': 9999999999},
])
def test_purchase_scope_tampering_never_calls_kms(setup_scope, change):
    s = setup_scope
    with pytest.raises(ValueError):
        s.signer.sign_execution(s.grant, s.signature, replace(s.purchase, **change))
    assert not s.execution.calls


@pytest.mark.parametrize('change', [
    {'chainId': 1}, {'nonce': 1}, {'nonce': 6}, {'value': 1},
    {'to': '0x' + '99' * 20}, {'gas': 500001}, {'gasPrice': 500000000001},
    {'from': '0x' + '99' * 20}, {'type': 2}, {'nonce': True},
    {'gas': 0}, {'data': '0x12345678'},
])
def test_gas_scope_tampering_never_calls_kms(setup_scope, change):
    s = setup_scope
    signature = s.signer.sign_execution(s.grant, s.signature, s.purchase)
    with pytest.raises(ValueError):
        s.signer.sign_transaction(transaction(s, signature) | change)
    assert not s.relayer.calls


def test_no_trailing_abi_or_wrong_execution_signature(setup_scope):
    s = setup_scope
    signature = s.signer.sign_execution(s.grant, s.signature, s.purchase)
    tx = transaction(s, signature)
    with pytest.raises(ValueError):
        s.signer.sign_transaction(tx | {'data': tx['data'] + '00' * 32})
    with pytest.raises(ValueError):
        s.signer.sign_transaction(transaction(s, bytes(65)))
    assert not s.relayer.calls


def test_backend_sends_structured_scope_and_verifies_returned_signatures(setup_scope):
    s = setup_scope
    backend = BudgetBackend(s.network, relayer_address=s.gas.address,
        execution_signer_address=s.execution_key.address, execution_sign=None,
        sign_execution=s.signer.sign_execution, transaction_sign=s.signer.sign_transaction)
    def rpc(method, params, **kwargs):
        return {'eth_gasPrice': hex(1000000000), 'eth_estimateGas': hex(100000)}[method]
    backend._checked = rpc
    result = backend.prepare({'purchase_id': 'order-1', 'quote_hash': 'd' * 64, 'amount_atomic': '300000'},
        SimpleNamespace(grant=s.grant, owner_signature=s.signature), 2)
    assert result['tx_hash'] == '0x' + keccak(bytes.fromhex(result['raw_transaction'][2:])).hex()
    assert len(s.execution.calls) == len(s.relayer.calls) == 1
