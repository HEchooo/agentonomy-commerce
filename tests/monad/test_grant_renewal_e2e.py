"""Run the real Monad grant-renewal acceptance scenario out of process."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "tests" / "monad" / "fixtures" / "grant_renewal_scenario.py"


def _safe_diagnostics(stdout: str, stderr: str) -> str:
    """Return short failure context without echoing signatures or key material."""

    text = "\n".join(part for part in (stderr, stdout) if part).strip()
    if not text:
        return "no child diagnostics"
    text = re.sub(r"0x[0-9a-fA-F]{64,130}", "<redacted>", text)
    lines = [
        line for line in text.splitlines()
        if not re.search(r"signature|private[_ -]?key|secret|credential|token", line, re.I)
    ]
    return "\n".join(lines[-12:])[:4000] or "diagnostics redacted"


def test_real_anvil_paid_order_survives_grant_renewal_and_new_generation(
    tmp_path: Path,
):
    # Pass only runtime basics. In particular, never inherit operator/cloud
    # credentials into the local fixture process.
    child_env = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "TMPDIR")
        if key in os.environ
    }
    # The scenario sets its own Core/Marketplace import paths. Keeping the
    # parent environment out of the child prevents legacy top-level module
    # names from changing the EVM fixture's protocol identity.
    child_env["PYTHONPATH"] = str(ROOT)
    try:
        result = subprocess.run(
            [sys.executable, str(SCENARIO), str(tmp_path)],
            cwd=ROOT,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"local Anvil grant-renewal scenario timed out: {exc.timeout}s")
    if result.returncode != 0:
        raise AssertionError(
            "local Anvil grant-renewal scenario failed "
            f"(exit {result.returncode}):\n{_safe_diagnostics(result.stdout, result.stderr)}"
        )
    assert "GRANT_RENEWAL_SCENARIO_OK" in result.stdout
