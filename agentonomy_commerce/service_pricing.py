"""Exact token-denominated service pricing helpers.

Service prices are represented in USDC's six decimal token units.  The
helpers in this module deliberately avoid float conversion and rounding so a
price in a preview, signed receipt, or status response has one deterministic
representation.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import re
from typing import Any


_ATOMIC_SCALE = 6
_ATOMIC_FACTOR = 10**_ATOMIC_SCALE
_DECIMAL_PATTERN = re.compile(r"^\d+(?:\.\d+)?$")
_ATOMIC_PATTERN = re.compile(r"^(?:0|[1-9]\d*)$")


def _reject_non_decimal(value: object) -> Decimal:
    """Parse a decimal string or Decimal without accepting lossy inputs."""

    if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
        raise ValueError("token amount must be a decimal string or Decimal")
    if isinstance(value, str):
        if _DECIMAL_PATTERN.fullmatch(value) is None:
            raise ValueError("token amount must use fixed-point decimal notation")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("token amount is invalid") from exc
    else:
        parsed = value
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("token amount must be finite and non-negative")
    return parsed


def _atomic_from_decimal(value: Decimal, *, allow_zero: bool) -> int:
    """Return an exact atomic integer, rejecting values needing rounding."""

    digits = value.as_tuple().digits
    exponent = value.as_tuple().exponent
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    if exponent >= 0:
        atomic = coefficient * (10**exponent) * _ATOMIC_FACTOR
    else:
        scale = -exponent
        if scale > _ATOMIC_SCALE:
            raise ValueError("token amount has more than six decimal places")
        else:
            atomic = coefficient * (10 ** (_ATOMIC_SCALE - scale))
    if not allow_zero and atomic <= 0:
        raise ValueError("token amount must be positive")
    return atomic


def _decimal_from_atomic(amount_atomic: int) -> Decimal:
    """Construct a Decimal with six fixed token decimal places exactly."""

    digits = tuple(int(digit) for digit in str(amount_atomic))
    return Decimal((0, digits, -_ATOMIC_SCALE))


def format_token_amount(value: Decimal | str, *, allow_zero: bool = True) -> str:
    """Format a token amount with at most six decimals and no rounding.

    The returned representation keeps at least two fractional digits for
    compatibility with the historical ``0.30`` service price.  Zero is
    accepted by default because this helper is also used for budget values;
    price-specific helpers enforce positivity separately.
    """

    parsed = _reject_non_decimal(value)
    atomic = _atomic_from_decimal(parsed, allow_zero=allow_zero)
    if atomic == 0:
        return "0.00"

    if parsed == 0:
        return "0.00"
    rendered = format(parsed, "f")
    integer, separator, fraction = rendered.partition(".")
    if separator:
        fraction = fraction.rstrip("0")
    if len(fraction) < 2:
        fraction += "0" * (2 - len(fraction))
    return f"{integer}.{fraction}"


def price_terms(price_usd: str) -> dict[str, str]:
    """Return canonical positive service price and six-decimal atomic amount."""

    if not isinstance(price_usd, str):
        raise ValueError("price_usd must be a decimal string")
    parsed = _reject_non_decimal(price_usd)
    atomic = _atomic_from_decimal(parsed, allow_zero=False)
    return {
        "price_usd": format_token_amount(parsed, allow_zero=False),
        "amount_atomic": str(atomic),
    }


def _canonical_atomic(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("amount_atomic must be an integer")
    if type(value) is int:
        atomic = value
    elif isinstance(value, str) and _ATOMIC_PATTERN.fullmatch(value):
        atomic = int(value)
    else:
        raise ValueError("amount_atomic must be a canonical integer")
    if atomic <= 0:
        raise ValueError("amount_atomic must be positive")
    if isinstance(value, str) and str(atomic) != value:
        raise ValueError("amount_atomic must be a canonical integer")
    return atomic


def atomic_price_terms(amount_atomic: int | str) -> dict[str, str]:
    """Return canonical price fields for a positive atomic integer."""

    atomic = _canonical_atomic(amount_atomic)
    return {
        "price_usd": format_token_amount(_decimal_from_atomic(atomic), allow_zero=False),
        "amount_atomic": str(atomic),
    }


def payment_terms(payment: Mapping[str, object]) -> dict[str, str]:
    """Validate that serialized decimal and atomic payment terms agree."""

    if not isinstance(payment, Mapping):
        raise ValueError("payment terms must be a mapping")
    if "price_usd" not in payment or "amount_atomic" not in payment:
        raise ValueError("payment terms require price_usd and amount_atomic")

    decimal_terms = price_terms(payment["price_usd"])
    atomic_terms = atomic_price_terms(payment["amount_atomic"])
    if decimal_terms["amount_atomic"] != atomic_terms["amount_atomic"]:
        raise ValueError("price_usd and amount_atomic do not agree")
    return decimal_terms

