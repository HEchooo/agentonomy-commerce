"""Service-price configuration checks in an isolated Monad worker process."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
WORKER = ROOT / "tests" / "monad" / "fixtures" / "service_price_configuration.py"


def _worker_env() -> dict[str, str]:
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG", "LC_ALL")
        if key in os.environ
    }
    env["PYTHONPATH"] = os.pathsep.join(
        str(ROOT / part)
        for part in (".", "apps/facilitator", "apps/core", "apps/node")
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_worker(mode: str, tmp_path: Path, *args: str) -> dict[str, str]:
    result = subprocess.run(
        [str(PYTHON), str(WORKER), mode, *args, str(tmp_path)],
        cwd=ROOT,
        env=_worker_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("amount_atomic", "price_usd"),
    [("1000", "0.001"), ("200", "0.0002"), ("300000", "0.30")],
)
def test_catalog_derives_exact_price_from_signer_scope(
    tmp_path: Path, amount_atomic: str, price_usd: str
):
    assert _run_worker("catalog", tmp_path, amount_atomic) == {
        "amount_atomic": amount_atomic,
        "price_usd": price_usd,
    }


def test_catalog_defaults_to_historical_price_without_signer_scope(tmp_path: Path):
    assert _run_worker("catalog_legacy", tmp_path) == {
        "amount_atomic": "300000",
        "price_usd": "0.30",
    }


@pytest.mark.parametrize("amount_atomic", ["0", "-1", "500001", "1000.0"])
def test_catalog_rejects_invalid_or_oversized_service_scope(
    tmp_path: Path, amount_atomic: str
):
    assert _run_worker("catalog_invalid", tmp_path, amount_atomic) == {
        "status": "rejected",
    }


def test_each_runtime_constructor_binds_the_configured_service_price(tmp_path: Path):
    assert _run_worker("constructors", tmp_path, "200") == {
        "prices": {
            "BudgetCommerceRuntime": {
                "amount_atomic": "200",
                "price_usd": "0.0002",
            },
            "PublicCommerceRuntime": {
                "amount_atomic": "200",
                "price_usd": "0.0002",
            },
            "HostedCommerceRuntime": {
                "amount_atomic": "200",
                "price_usd": "0.0002",
            },
        }
    }


def test_hosted_catalog_refresh_does_not_rewrite_historical_preview_payment(
    tmp_path: Path,
):
    assert _run_worker("hosted_historical", tmp_path) == {
        "current": {"amount_atomic": "1000", "price_usd": "0.001"},
        "historical": {"amount_atomic": "300000", "price_usd": "0.30"},
    }


@pytest.mark.parametrize(
    ("amount_atomic", "expected_price"),
    [("1000", "0.001"), ("200", "0.0002"), ("300000", "0.30")],
)
def test_public_canary_status_reports_scope_price(
    tmp_path: Path, amount_atomic: str, expected_price: str
):
    assert _run_worker("canary_status", tmp_path, amount_atomic) == {
        "price": expected_price,
    }


def test_external_core_snapshot_keeps_six_decimal_budget_values(tmp_path: Path):
    assert _run_worker("external_snapshot", tmp_path) == {
        "budget_usdc": "0.901",
        "used_amount_usdc": "0.802",
        "reserved_amount_usdc": "0.00",
    }


def test_budget_runtime_snapshot_does_not_round_remaining_amount(tmp_path: Path):
    assert _run_worker("budget_snapshot", tmp_path) == {
        "budget_usdc": "0.901",
        "used_amount_usdc": "0.802",
        "reserved_amount_usdc": "0.00",
        "remaining_amount_usdc": "0.099",
    }
