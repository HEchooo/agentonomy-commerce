"""Run Marketplace-only paid-input recovery tests in a clean interpreter.

The Monad suite imports Core and Marketplace modules that both expose a
top-level ``services`` package.  The suite's normal Core-first ``PYTHONPATH``
would resolve the wrong package for these Marketplace unit tests, so keep the
cases in a subprocess with Marketplace first on its import path.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests" / "monad" / "fixtures" / "paid_input_recovery_cases.py"
_MAX_DIAGNOSTIC_BYTES = 12_000


def test_paid_input_recovery_cases_in_marketplace_namespace() -> None:
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
        [sys.executable, "-m", "pytest", "-q", str(CASES)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode == 0:
        return
    stdout = completed.stdout[-_MAX_DIAGNOSTIC_BYTES:]
    stderr = completed.stderr[-_MAX_DIAGNOSTIC_BYTES:]
    raise AssertionError(
        "paid-input recovery subprocess failed "
        f"(exit={completed.returncode})\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
