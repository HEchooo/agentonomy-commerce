"""Price changes preserve each original order's frozen payment scope."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from examples.monad_commerce.hosted_canary import HostedCanary
from examples.monad_commerce.hosted_service import HostedCommerceService

OWNER, TOKEN, EXECUTOR, PAYEE = ['0x' + c * 40 for c in '1234']


def payment(amount):
    return {'price_usd': format(Decimal(amount) / 1000000, 'f'),
            'amount_atomic': str(amount), 'network': 'eip155:10143',
            'asset': TOKEN, 'pay_to': PAYEE}


def proof(amount):
    return {'purchase_id': 'purchase_order', 'preview_id': 'preview_order',
            'settlement': dict(verified=True, status='verified', two_rpc_verified=True,
                chain_id=10143, receipt_status=1, transaction_hash='0x' + 'ab' * 32,
                executor=EXECUTOR, owner=OWNER, token=TOKEN, payee=PAYEE,
                amount_atomic=str(amount), purchase_commitment='0x' + 'cd' * 32,
                quote_commitment='0x' + 'ef' * 32, grant_hash='0x' + 'aa' * 32,
                block_hash='0x' + 'bb' * 32, finality_block_hash='0x' + 'cc' * 32)}


def canary_for(original_payment):
    canary = HostedCanary.__new__(HostedCanary)
    canary.tenant_id, canary.owner = 'tenant', OWNER
    canary.network = SimpleNamespace(token=TOKEN, executor=EXECUTOR, payee=PAYEE,
                                     network='eip155:10143')
    canary.market = SimpleNamespace(request=lambda method, args: {
        'preview_id': 'preview_order', 'payment': original_payment,
    })
    return canary


@pytest.mark.parametrize('amount', [1000, 200, 300000])
def test_gate_accepts_current_and_historical_prices_only_from_original_preview(amount):
    canary = canary_for(payment(amount))
    assert canary._verified_gate_proof(proof(amount), 'purchase_order')['amount_atomic'] == str(amount)


def test_gate_rejects_receipt_price_for_a_different_frozen_quote():
    canary = canary_for(payment(1000))
    with pytest.raises(ValueError, match='incomplete|payment|preview'):
        canary._verified_gate_proof(proof(300000), 'purchase_order')


@pytest.mark.parametrize('amount', [1000, 200, 300000])
def test_service_identity_uses_exact_frozen_price_without_repricing_history(amount):
    service = HostedCommerceService.__new__(HostedCommerceService)
    service.registry = object()
    service.scope = SimpleNamespace(network=SimpleNamespace(
        network='eip155:10143', token=TOKEN, payee=PAYEE))
    service._service_identity = lambda: {'verified': True, 'agent_wallet': PAYEE}
    canary = canary_for(payment(amount))
    canary._start_market = lambda: None
    service._identity_guard(canary, 'execute', {'preview_id': 'preview_order'})


@pytest.mark.parametrize('change', [
    {'amount_atomic': '999'}, {'price_usd': '0.000999'},
    {'pay_to': OWNER}, {'asset': OWNER}, {'price_usd': '0.0000001'},
])
def test_service_identity_fails_closed_on_mismatched_payment(change):
    service = HostedCommerceService.__new__(HostedCommerceService)
    service.registry = object()
    service.scope = SimpleNamespace(network=SimpleNamespace(
        network='eip155:10143', token=TOKEN, payee=PAYEE))
    service._service_identity = lambda: {'verified': True, 'agent_wallet': PAYEE}
    canary = canary_for(payment(1000) | change)
    canary._start_market = lambda: None
    with pytest.raises(ValueError):
        service._identity_guard(canary, 'execute', {'preview_id': 'preview_order'})
