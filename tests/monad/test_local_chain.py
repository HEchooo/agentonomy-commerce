from examples.monad_commerce.local_chain import LocalChain
from eth_abi import encode
from eth_utils import keccak

def test_real_local_erc20_deployment_and_allowance():
    with LocalChain() as chain:
        token, executor = chain.deploy()
        assert int(chain.rpc.call('eth_chainId', []), 16) == 31337
        assert chain.rpc.call('eth_getCode', [executor, 'latest']) != '0x'
        approve = chain.approve(1_000_000)
        assert approve['status'] == '0x1'
        data = '0x' + (keccak(text='allowance(address,address)')[:4] + encode(['address','address'],[chain.owner.address,executor])).hex()
        assert int(chain.rpc.call('eth_call',[{'to':token,'data':data},'latest']),16) == 1_000_000
