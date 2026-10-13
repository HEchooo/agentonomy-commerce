import pytest

from examples.monad_commerce.core_worker import _money


@pytest.mark.parametrize('value,expected', [
    ('0.001', '0.001'), ('0.0002', '0.0002'),
    ('0.901', '0.901'), ('0.099', '0.099'),
    ('0.30', '0.30'), ('0', '0.00'),
])
def test_core_budget_display_preserves_exact_token_precision(value, expected):
    assert _money(value) == expected
