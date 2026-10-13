from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).with_name("fixtures") / "micro_price_scenario.py"
_MAX_DIAGNOSTIC_BYTES = 16_000


def _run_micro_price_case(tmp_path: Path, case: str) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
    }
    env["PYTHONPATH"] = os.pathsep.join(
        (
            str(ROOT / "apps" / "marketplace"),
            str(ROOT),
            str(ROOT / "apps" / "facilitator"),
            str(ROOT / "apps" / "core"),
            str(ROOT / "apps" / "node"),
            str(ROOT / "tests" / "monad" / "fixtures"),
        )
    )
    completed = subprocess.run(
        [sys.executable, str(RUNNER), case, str(tmp_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    if completed.returncode == 0:
        return
    stdout = completed.stdout[-_MAX_DIAGNOSTIC_BYTES:]
    stderr = completed.stderr[-_MAX_DIAGNOSTIC_BYTES:]
    raise AssertionError(
        f"micro-price subprocess failed for {case!r} "
        f"(exit={completed.returncode})\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_external_wallet_delivers_and_replays_exact_001_without_second_charge(
    tmp_path: Path,
) -> None:
    _run_micro_price_case(tmp_path, "exact_001_delivery_replay")


def test_external_wallet_delivers_and_replays_exact_0002_without_second_charge(
    tmp_path: Path,
) -> None:
    _run_micro_price_case(tmp_path, "exact_0002_delivery_replay")


def test_original_030_paid_delivery_recovers_after_catalog_price_change(
    tmp_path: Path,
) -> None:
    _run_micro_price_case(tmp_path, "legacy_030_recovery_after_price_change")
