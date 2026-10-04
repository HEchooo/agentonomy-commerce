from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).with_name("fixtures") / "external_loop_runner.py"
_MAX_DIAGNOSTIC_BYTES = 12_000


def _run_external_case(tmp_path: Path, case: str) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
    }
    env["PYTHONPATH"] = os.pathsep.join(
        (
            str(ROOT / "apps" / "marketplace"),
            str(ROOT),
            str(ROOT / "apps" / "core"),
        )
    )
    completed = subprocess.run(
        [sys.executable, str(RUNNER), case, str(tmp_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode == 0:
        return
    stdout = completed.stdout[-_MAX_DIAGNOSTIC_BYTES:]
    stderr = completed.stderr[-_MAX_DIAGNOSTIC_BYTES:]
    raise AssertionError(
        f"external-wallet subprocess failed for {case!r} "
        f"(exit={completed.returncode})\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_external_wallet_delivers_and_replays_after_restart_without_new_gas_payment(
    tmp_path: Path,
) -> None:
    _run_external_case(
        tmp_path,
        "delivers_and_replays_after_restart_without_new_gas_payment",
    )


def test_external_wallet_core_revoke_keeps_previous_payment_readable_and_reconcilable(
    tmp_path: Path,
) -> None:
    _run_external_case(
        tmp_path,
        "core_revoke_keeps_previous_payment_readable_and_reconcilable",
    )


def test_external_wallet_payment_submitted_restart_recovers_by_purchase_id(
    tmp_path: Path,
) -> None:
    _run_external_case(
        tmp_path,
        "payment_submitted_restart_recovers_by_purchase_id",
    )


def test_external_wallet_paid_delivery_failure_restarts_and_recovers_same_order(
    tmp_path: Path,
) -> None:
    _run_external_case(
        tmp_path,
        "paid_delivery_failure_restarts_and_recovers_same_order",
    )
