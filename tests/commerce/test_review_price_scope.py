"""A merchant's expected fee belongs to an original immutable preview."""
from types import SimpleNamespace

import pytest

from agentonomy_commerce.runtime import ReviewRuntime, digest


def runtime_for(amount='0.30', atomic='300000'):
    payment = {'price_usd': amount, 'amount_atomic': atomic}
    preview = SimpleNamespace(preview_id='preview_original', user_id='owner',
        offering_id='service', input_hash='input', payment=payment,
        quote_hash=digest(payment))
    purchase = SimpleNamespace(purchase_id='purchase_original', preview_id=preview.preview_id,
        user_id=preview.user_id, offering_id=preview.offering_id, input_hash=preview.input_hash)
    runtime = ReviewRuntime.__new__(ReviewRuntime)
    runtime.repository = SimpleNamespace(get_purchase=lambda _: purchase,
                                          get_preview=lambda _: preview)
    return runtime, purchase, preview


@pytest.mark.parametrize('price,atomic', [('0.30', '300000'), ('0.001', '1000'), ('0.0002', '200')])
def test_expected_price_comes_from_original_preview(price, atomic):
    runtime, _, _ = runtime_for(price, atomic)
    assert runtime._expected_payment('purchase_original') == {'price_usd': price, 'amount_atomic': atomic}


@pytest.mark.parametrize('field', ['preview_id', 'user_id', 'offering_id', 'input_hash'])
def test_cross_order_price_scope_is_unavailable(field):
    runtime, purchase, _ = runtime_for()
    setattr(purchase, field, 'different')
    assert runtime._expected_payment('purchase_original') is None


def test_changed_payment_without_matching_frozen_quote_hash_is_unavailable():
    runtime, _, preview = runtime_for()
    preview.payment = {'price_usd': '0.001', 'amount_atomic': '1000'}
    assert runtime._expected_payment('purchase_original') is None
