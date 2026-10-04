import copy
import pytest
from agentonomy_commerce.budget_network import NetworkConfig
from agentonomy_commerce.wallet_transactions import verify_wallet_transaction
from agentonomy_commerce import wallet_transactions


def observations():
    transaction = {'hash': '0x' + 'aa' * 32, 'from': '0x' + '44' * 20,
        'to': '0x' + '11' * 20, 'value': '0x0', 'input': '0x1234',
        'chainId': '0x279f', 'blockNumber': '0xa', 'blockHash': '0x' + 'bb' * 32,
        'nonce': '0x0'}
    receipt = {'transactionHash': transaction['hash'], 'from': transaction['from'],
        'to': transaction['to'], 'status': '0x1', 'blockNumber': '0xa',
        'blockHash': transaction['blockHash']}
    row = {'chain_id': '0x279f', 'transaction': transaction, 'receipt': receipt,
        'canonical_block': {'number': '0xa', 'hash': transaction['blockHash']},
        'finality': {'verified': True, 'kind': 'monad_verified',
                     'canonical_block': {'number': '0xc', 'hash': '0x' + 'cc' * 32}}}
    return [copy.deepcopy(row), copy.deepcopy(row)]


def cfg():
    return NetworkConfig('monad_testnet', 10143, ('https://a.example', 'https://b.example'),
        '0x' + '11' * 20, '0x' + '22' * 20, '0x' + '33' * 20)


def verify():
    return verify_wallet_transaction(cfg(), owner='0x' + '44' * 20,
        tx_hash='0x' + 'aa' * 32, to='0x' + '11' * 20, data='0x1234')


def test_wallet_receipt_requires_two_canonical_verified_observations(monkeypatch):
    monkeypatch.setattr(wallet_transactions, 'fetch_observations', lambda *a: observations())
    result = verify()
    assert result['two_rpc_verified'] and result['block_number'] == 10
    monkeypatch.setattr(wallet_transactions, 'fetch_observations', lambda *a: None)
    assert verify() is None


@pytest.mark.parametrize('part,key,value', [
    ('transaction','from','0x'+'99'*20), ('transaction','to','0x'+'99'*20),
    ('transaction','input','0x1235'), ('transaction','value','0x1'),
    ('transaction','chainId','0x1'), ('transaction','hash','0x'+'99'*32),
    ('receipt','status','0x0'), ('receipt','blockHash','0x'+'99'*32),
    ('receipt','transactionHash','0x'+'99'*32), ('canonical_block','hash','0x'+'99'*32),
])
def test_wrong_wallet_transaction_never_accepts(monkeypatch, part, key, value):
    rows = observations()
    rows[1][part][key] = value
    monkeypatch.setattr(wallet_transactions, 'fetch_observations', lambda *a: rows)
    with pytest.raises(ValueError):
        verify()


def test_disagreeing_verified_boundary_rejected(monkeypatch):
    rows = observations()
    rows[1]['finality']['canonical_block']['hash'] = '0x' + '99' * 32
    monkeypatch.setattr(wallet_transactions, 'fetch_observations', lambda *a: rows)
    with pytest.raises(ValueError):
        verify()


@pytest.mark.parametrize('reason', ['owner', 'data', 'reverted'])
def test_only_agreeing_canonical_invalid_transactions_are_replaceable(monkeypatch, reason):
    rows = observations()
    for row in rows:
        if reason == 'owner':
            row['transaction']['from'] = row['receipt']['from'] = '0x' + '99' * 20
        elif reason == 'data':
            row['transaction']['input'] = '0x5678'
        else:
            row['receipt']['status'] = '0x0'
    monkeypatch.setattr(wallet_transactions, 'fetch_observations', lambda *a: rows)
    with pytest.raises(wallet_transactions.InvalidWalletTransaction):
        verify()
    rows[1]['finality']['canonical_block']['hash'] = '0x' + '99' * 32
    with pytest.raises(ValueError) as error:
        verify()
    assert not isinstance(error.value, wallet_transactions.InvalidWalletTransaction)


@pytest.mark.parametrize('part,key,value', [
    ('transaction', 'input', None), ('transaction', 'input', {}),
    ('transaction', 'input', '0x123'), ('transaction', 'input', '0xzz'),
    ('receipt', 'status', '0x2'),
])
def test_agreeing_malformed_evidence_cannot_release_pending_hash(monkeypatch, part, key, value):
    rows = observations()
    for row in rows:
        row[part][key] = value
    monkeypatch.setattr(wallet_transactions, 'fetch_observations', lambda *a: rows)
    with pytest.raises(ValueError) as error:
        verify()
    assert not isinstance(error.value, wallet_transactions.InvalidWalletTransaction)


def test_equivalent_hex_data_casing_does_not_prove_invalid_transaction(monkeypatch):
    rows = observations()
    rows[0]['transaction']['input'] = '0xabCD'
    rows[1]['transaction']['input'] = '0xABcd'
    monkeypatch.setattr(wallet_transactions, 'fetch_observations', lambda *a: rows)
    assert verify_wallet_transaction(cfg(), owner='0x' + '44' * 20,
        tx_hash='0x' + 'aa' * 32, to='0x' + '11' * 20, data='0xAbCd')['verified']
