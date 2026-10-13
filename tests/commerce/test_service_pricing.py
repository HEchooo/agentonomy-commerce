from __future__ import annotations

import pytest
from decimal import Decimal

from agentonomy_commerce.service_pricing import (
    atomic_price_terms,
    format_token_amount,
    payment_terms,
    price_terms,
)


@pytest.mark.parametrize(
    ("price_usd", "expected"),
    [
        (
            "0.30",
            {"price_usd": "0.30", "amount_atomic": "300000"},
        ),
        (
            "0.001",
            {"price_usd": "0.001", "amount_atomic": "1000"},
        ),
        (
            "0.0002",
            {"price_usd": "0.0002", "amount_atomic": "200"},
        ),
    ],
)
def test_price_terms_uses_exact_six_decimal_atomic_conversion(price_usd, expected):
    assert price_terms(price_usd) == expected


@pytest.mark.parametrize(
    "price_usd",
    [
        0.30,
        True,
        "0",
        "-0.001",
        "1e-3",
        "0.0000001",
    ],
)
def test_price_terms_rejects_non_positive_or_non_canonical_precision(price_usd):
    with pytest.raises(ValueError):
        price_terms(price_usd)


def test_payment_terms_requires_matching_decimal_and_atomic_values():
    assert payment_terms({"price_usd": "0.001", "amount_atomic": "1000"}) == {
        "price_usd": "0.001",
        "amount_atomic": "1000",
    }

    with pytest.raises(ValueError):
        payment_terms({"price_usd": "0.001", "amount_atomic": "200"})


@pytest.mark.parametrize(
    ("amount_atomic", "expected"),
    [
        (300000, {"price_usd": "0.30", "amount_atomic": "300000"}),
        ("1000", {"price_usd": "0.001", "amount_atomic": "1000"}),
        ("200", {"price_usd": "0.0002", "amount_atomic": "200"}),
    ],
)
def test_atomic_price_terms_uses_exact_token_scale(amount_atomic, expected):
    assert atomic_price_terms(amount_atomic) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0"), "0.00"),
        ("0", "0.00"),
        ("0.30", "0.30"),
        ("0.001000", "0.001"),
        ("1.230000", "1.23"),
    ],
)
def test_format_token_amount_is_exact_and_keeps_two_display_decimals(value, expected):
    assert format_token_amount(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        0.3,
        True,
        "1e-3",
        "0.0000001",
        "0.3000000",
        "-0.001",
    ],
)
def test_token_amount_helpers_reject_float_exponent_overprecision_and_negative(value):
    with pytest.raises(ValueError):
        format_token_amount(value)


@pytest.mark.parametrize("amount_atomic", [0, "0", "0300000", "1e3", -1, True, 1.5])
def test_atomic_price_terms_requires_canonical_positive_integer(amount_atomic):
    with pytest.raises(ValueError):
        atomic_price_terms(amount_atomic)
