import pytest
from scripts.monad.verify_evidence import verify
from agentonomy_commerce.budget_network import RpcClient


def test_evidence_command_rejects_scope_before_any_rpc(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('must not access RPC on wrong scope')
    monkeypatch.setattr(RpcClient, 'call', forbidden)
    config=dict(mode='local_anvil',chain_id=31337,rpc_urls=['http://127.0.0.1:1']*2,
        token='0x'+'11'*20,executor='0x'+'22'*20,payee='0x'+'33'*20)
    expected=dict(tx_hash='0x'+'44'*32,chain_id=10143,executor=config['executor'],
        owner='0x'+'55'*20,payee=config['payee'],token=config['token'],amount='1',
        grant_hash='0x'+'66'*32,grant_id='0x'+'77'*32,purchase_id='0x'+'88'*32,
        quote_hash='0x'+'99'*32,calldata='0x1234')
    with pytest.raises(ValueError, match='trusted deployment'):
        verify(config,expected)
