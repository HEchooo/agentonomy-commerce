from types import SimpleNamespace

import pytest

from agentonomy_commerce.wallet_transactions import InvalidWalletTransaction
from examples.monad_commerce import public_canary


@pytest.fixture
def canary(tmp_path):
    instance = public_canary.PublicCanary.__new__(public_canary.PublicCanary)
    instance.wallet_operations = {'revocation': {'core_revoked': True, 'chain_grant_id': '0x' + '12' * 32}}
    instance.operations_path = tmp_path / 'wallet-operations.json'
    instance.network = SimpleNamespace(token='0x' + '11' * 20, executor='0x' + '22' * 20)
    instance.owner = '0x' + '44' * 20
    instance.onboarding = lambda *args, **kwargs: {'phase': 'ready'}
    return instance


@pytest.mark.parametrize('operation', ['approval', 'revocation'])
def test_proven_invalid_hash_does_not_block_correct_hash(canary, monkeypatch, operation):
    wrong, correct = '0x' + 'aa' * 32, '0x' + 'bb' * 32
    def verify(*args, tx_hash, **kwargs):
        if tx_hash == wrong:
            raise InvalidWalletTransaction('proven unrelated transaction')
        return {'verified': True, 'transaction_hash': tx_hash}
    monkeypatch.setattr(public_canary, 'verify_wallet_transaction', verify)
    with pytest.raises(InvalidWalletTransaction):
        getattr(canary, 'verify_' + operation)(wrong)
    assert not canary.wallet_operations[operation].get('transaction_hash')
    result = getattr(canary, 'verify_' + operation)(correct)
    assert result['verified'] and result['transaction_hash'] == correct
    if operation == 'revocation':
        assert result['core_revoked'] and result['chain_revoked']


@pytest.mark.parametrize('operation', ['approval', 'revocation'])
@pytest.mark.parametrize('outcome', ['pending', 'unavailable', 'disagree'])
def test_unknown_hash_stays_pinned_until_independent_resolution(canary, monkeypatch, operation, outcome):
    original, replacement = '0x' + 'aa' * 32, '0x' + 'bb' * 32
    def verify(*args, **kwargs):
        if outcome == 'unavailable':
            raise RuntimeError('RPC unavailable')
        if outcome == 'disagree':
            raise ValueError('RPC disagreement')
        return None
    monkeypatch.setattr(public_canary, 'verify_wallet_transaction', verify)
    if outcome == 'pending':
        assert getattr(canary, 'verify_' + operation)(original)['status'] == 'pending'
    else:
        with pytest.raises((ValueError, RuntimeError)):
            getattr(canary, 'verify_' + operation)(original)
    with pytest.raises(ValueError, match='do not replace'):
        getattr(canary, 'verify_' + operation)(replacement)
    assert canary.wallet_operations[operation]['transaction_hash'] == original


def test_previous_pending_candidate_can_be_cleared_only_when_proven_invalid(canary, monkeypatch):
    tx_hash = '0x' + 'aa' * 32
    monkeypatch.setattr(public_canary, 'verify_wallet_transaction', lambda *a, **kw: None)
    canary.verify_approval(tx_hash)
    def reject(*args, **kwargs):
        raise InvalidWalletTransaction('canonical reverted transaction')
    monkeypatch.setattr(public_canary, 'verify_wallet_transaction', reject)
    with pytest.raises(InvalidWalletTransaction):
        canary.verify_approval(tx_hash)
    assert canary.wallet_operations['approval']['rejected_transaction_hash'] == tx_hash
    assert 'transaction_hash' not in canary.wallet_operations['approval']


def test_corrected_hash_is_pending_not_previous_rejected_status(canary, monkeypatch):
    wrong, correct = '0x' + 'aa' * 32, '0x' + 'bb' * 32
    def verify(*args, tx_hash, **kwargs):
        if tx_hash == wrong:
            raise InvalidWalletTransaction('proven invalid')
        return None
    monkeypatch.setattr(public_canary, 'verify_wallet_transaction', verify)
    with pytest.raises(InvalidWalletTransaction):
        canary.verify_approval(wrong)
    canary.verify_approval(correct)
    assert canary.wallet_operations['approval']['status'] == 'pending'
    assert canary.wallet_operations['approval']['transaction_hash'] == correct
    assert canary.wallet_operations['approval']['rejected_transaction_hash'] == wrong
