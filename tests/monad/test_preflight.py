from scripts.monad.preflight import inspect_configuration

def test_missing_public_addresses_is_a_reviewable_block_not_a_default_deployment():
    report=inspect_configuration({'mode':'monad_testnet','chain_id':10143})
    assert report['status']=='needs_configuration'
    assert report['broadcast'] is False
    assert 'token' in report['missing'] and 'executor' in report['missing']

def test_dry_run_never_claims_rpc_or_onchain_acceptance():
    cfg=dict(mode='local_anvil',chain_id=31337,rpc_urls=['http://127.0.0.1:8545']*2,
        token='0x'+'11'*20,executor='0x'+'22'*20,payee='0x'+'33'*20)
    report=inspect_configuration(cfg)
    assert report['status']=='configuration_valid'
    assert not report['rpc_checked'] and not report['public_acceptance']


def test_rpc_preflight_requires_same_canonical_boundary(monkeypatch):
    import pytest
    from eth_abi import encode
    from eth_utils import keccak
    from scripts.monad import preflight

    token, executor, payee = ('0x' + c * 40 for c in '123')
    code = '0x6000'
    expected_hash = '0x' + keccak(bytes.fromhex(code[2:])).hex()
    cfg = dict(mode='monad_testnet', chain_id=10143,
               rpc_urls=['https://one.example', 'https://two.example'],
               token=token, executor=executor, payee=payee,
               token_code_hash=expected_hash, executor_code_hash=expected_hash)
    answers = {keccak(text=s)[:4].hex(): encode([typ], [value]).hex()
               for s, typ, value in [('decimals()', 'uint8', 6),
                   ('symbol()', 'string', 'TestUSD'), ('TOKEN()', 'address', token),
                   ('EXECUTION_CHAIN_ID()', 'uint256', 10143)]}
    divergent = False
    calls = []
    class Rpc:
        def __init__(self, url): self.second = 'two' in url
        def check_chain(self, chain): assert chain == 10143
        def call(self, method, params):
            calls.append((method, params))
            if method == 'eth_getBlockByNumber':
                if params[0] == 'finalized':
                    return {'number': hex(103 if self.second else 100)}
                assert params[0] == hex(97)
                return {'number': hex(97), 'hash': '0x' + ('bb' if divergent and self.second else 'aa') * 32}
            if method == 'eth_getCode': return code
            if method == 'eth_call': return '0x' + answers[params[0]['data'][2:]]
            raise AssertionError(method)
    monkeypatch.setattr(preflight, 'RpcClient', Rpc)
    report = inspect_configuration(cfg, rpc_check=True)
    assert report['observations'][0]['canonical_boundary'] == report['observations'][1]['canonical_boundary']
    assert report['observations'][0]['verified_boundary'] == 97
    assert all(params[-1] == hex(97) for method, params in calls if method in {'eth_call', 'eth_getCode'})
    divergent = True
    with pytest.raises(ValueError, match='boundary'):
        inspect_configuration(cfg, rpc_check=True)
