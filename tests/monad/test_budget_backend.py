from types import SimpleNamespace
import time
import copy
import pytest
from eth_utils import keccak
from apps.facilitator.budget_protocol import SpendGrant, sign_grant
from agentonomy_commerce.budget_backend import BudgetBackend
from agentonomy_commerce.budget_network import NetworkConfig
from examples.monad_commerce.local_chain import LocalChain
from eth_keys import keys


def test_real_budget_payment_is_verified_and_never_replaced():
    with LocalChain() as chain:
        token, executor = chain.deploy()
        chain.approve(1_000_000)
        cfg=NetworkConfig('local_anvil',31337,(chain.url,chain.url),token,executor,chain.payee)
        now=int(time.time())
        grant=SpendGrant('0x'+'aa'*32,chain.owner.address,'0x'+'bb'*32,token,chain.payee,500_000,1_000_000,now-60,now+3600,chain.execution_signer.address)
        binding=SimpleNamespace(grant=grant.to_json(), owner_signature='0x'+sign_grant(grant,chain.owner.key,31337,executor).hex())
        backend=BudgetBackend(cfg, relayer_address=chain.relayer.address,
            execution_signer_address=chain.execution_signer.address,
            execution_sign=lambda digest: sign_digest(digest,chain.execution_signer.key),
            transaction_sign=lambda tx: chain.relayer.sign_transaction(tx).raw_transaction)
        row={'purchase_id':'purchase-1','quote_hash':'a'*64,'amount_atomic':'300000'}
        attempt=backend.prepare(row,binding,backend.pending_nonce())
        assert backend.verify(row,binding,attempt) is None
        assert backend.broadcast(attempt)==attempt['tx_hash']
        chain.mine(5)
        evidence=backend.verify(row,binding,attempt)
        assert evidence and evidence['transaction_hash']==attempt['tx_hash']
        assert evidence['amount_atomic']=='300000'
        assert backend.allowance(chain.owner.address)==700_000
        again = backend.verify(row,binding,attempt)
        assert again['transaction_hash'] == evidence['transaction_hash']
        assert again['block_hash'] == evidence['block_hash']
        assert again['finality_block_number'] >= evidence['finality_block_number']
        changed = copy.deepcopy(attempt)
        changed['execution']['deadline'] = str(int(changed['execution']['deadline']) + 1)
        with pytest.raises(ValueError, match='digest'):
            backend.verify(row, binding, changed)
        changed = copy.deepcopy(attempt)
        changed['execution_digest'] = '0x' + '11' * 32
        with pytest.raises(ValueError, match='digest'):
            backend.verify(row, binding, changed)
        # Prepare while allowance exists; revoke it before the signed tx mines.
        second = row | {'purchase_id': 'purchase-reverted'}
        failed_attempt = backend.prepare(second, binding, backend.pending_nonce())
        chain.approve(0)
        backend.broadcast(failed_attempt)
        chain.mine(5)
        failed = backend.verify(second, binding, failed_attempt)
        assert failed['status'] == 'reverted'
        assert failed['receipt_status'] == 0 and failed['two_rpc_verified'] is True
        assert failed['transaction_hash'] == failed_attempt['tx_hash']



def sign_digest(digest,key):
    sig=keys.PrivateKey(bytes(key)).sign_msg_hash(digest)
    return sig.r.to_bytes(32,'big')+sig.s.to_bytes(32,'big')+bytes([sig.v+27])
