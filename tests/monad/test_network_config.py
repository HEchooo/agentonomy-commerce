import pytest
from agentonomy_commerce.budget_network import NetworkConfig, RpcClient, verified_boundary

TOKEN = '0x' + '11' * 20
EXECUTOR = '0x' + '22' * 20
PAYEE = '0x' + '33' * 20

def config(**changes):
    return NetworkConfig(**dict(mode='local_anvil', chain_id=31337,
        rpc_urls=('http://127.0.0.1:8545', 'http://127.0.0.1:8546'),
        token=TOKEN, executor=EXECUTOR, payee=PAYEE, **changes))

def test_explicit_local_network_is_valid():
    assert config().network == 'eip155:31337'

@pytest.mark.parametrize('change', [
    {'mode': 'mainnet'}, {'chain_id': 137}, {'token': '0x' + '00'*20},
    {'rpc_urls': ('http://example.com', 'http://127.0.0.1:8545')},
    {'rpc_urls': ('http://user:pass@127.0.0.1:8545',)*2},
    {'token_decimals': 18}, {'gas_limit': True},
])
def test_invalid_network_fail_closed(change):
    values = dict(mode='local_anvil', chain_id=31337,
        rpc_urls=('http://127.0.0.1:8545','http://127.0.0.1:8546'),
        token=TOKEN, executor=EXECUTOR, payee=PAYEE)
    with pytest.raises(ValueError): NetworkConfig(**(values | change))

def test_public_mode_requires_two_distinct_https_endpoints_and_verified_delay():
    with pytest.raises(ValueError):
        NetworkConfig('monad_testnet', 10143, ('https://rpc.example',)*2, TOKEN, EXECUTOR, PAYEE)
    public = NetworkConfig('monad_testnet', 10143, ('https://rpc1.example','https://rpc2.example'), TOKEN, EXECUTOR, PAYEE)
    assert verified_boundary(public, 50) == 47
    assert verified_boundary(config(), 50) == 50
    with pytest.raises(ValueError): verified_boundary(public, 2)

def test_preflight_rpc_cannot_send_or_sign():
    rpc = RpcClient('http://127.0.0.1:8545', writable=False)
    for name in ['eth_sendRawTransaction','eth_sendTransaction','personal_sign','anvil_setBalance','evm_mine']:
        with pytest.raises(ValueError): rpc.call(name, [])
