"""Hosted payment admission expiry checks across a SQLite round trip."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _run_subprocess(script: str, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-c", script, *args],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return result


def _run_repository_roundtrip(expires_at: str, state_dir: Path) -> dict:
    result = _run_subprocess(
        _REPOSITORY_SCENARIO,
        [expires_at, str(state_dir)],
        _worker_env(marketplace_first=True),
    )
    assert result.returncode == 0, (
        "Marketplace SQLite fixture failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


def _run_canary(case: str, preview: dict, state_dir: Path) -> dict:
    result = _run_subprocess(
        _CANARY_SCENARIO,
        [case, json.dumps(preview, separators=(",", ":")), str(state_dir)],
        _worker_env(marketplace_first=False),
    )
    assert result.returncode == 0, (
        f"{case} failed before producing the admission result:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


def test_sqlite_roundtrip_future_naive_utc_preview_enters_gate_once(tmp_path):
    expires_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    preview = _run_repository_roundtrip(expires_at, tmp_path / "future")
    result = _run_canary("future_naive", preview, tmp_path / "future")
    assert result == {"gate_calls": 1, "execute_calls": 1}


def test_sqlite_roundtrip_expired_naive_utc_preview_fails_closed(tmp_path):
    expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    preview = _run_repository_roundtrip(expires_at, tmp_path / "expired")
    result = _run_canary("expired_naive", preview, tmp_path / "expired")
    assert result == {"error": "ValueError", "gate_calls": 0, "execute_calls": 0}


def test_aware_and_explicit_offset_preview_expiry_still_enters_gate(tmp_path):
    now = datetime.now(UTC)
    assert _run_canary("aware", {
        "preview_id": "preview_sqlite",
        "opc_installation_id": None,
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
    }, tmp_path / "aware") == {
        "gate_calls": 1,
        "execute_calls": 1,
    }
    assert _run_canary("explicit_offset", {
        "preview_id": "preview_sqlite",
        "opc_installation_id": None,
        "expires_at": (now + timedelta(minutes=5)).astimezone(
            timezone(timedelta(hours=8))
        ).isoformat(),
    }, tmp_path / "offset") == {
        "gate_calls": 1,
        "execute_calls": 1,
    }


def test_invalid_preview_expiry_fails_closed_before_gate_or_dispatch(tmp_path):
    result = _run_canary("invalid", {
        "preview_id": "preview_sqlite",
        "opc_installation_id": None,
        "expires_at": "not-a-timestamp",
    }, tmp_path / "invalid")
    assert result == {"error": "ValueError", "gate_calls": 0, "execute_calls": 0}


def _worker_env(*, marketplace_first: bool) -> dict[str, str]:
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG", "LC_ALL")
        if key in os.environ
    }
    roots = [
        ROOT / "apps" / "marketplace",
        ROOT / "apps" / "core",
        ROOT / "apps" / "node",
        ROOT,
    ]
    if not marketplace_first:
        roots[0], roots[1] = roots[1], roots[0]
    env["PYTHONPATH"] = os.pathsep.join(str(root) for root in roots)
    return env


_REPOSITORY_SCENARIO = r'''
from datetime import datetime
import json
from pathlib import Path
import sys

from services.marketplace_repository import MarketplaceRepository
from shared.commerce import PurchasePreview

expires_at = datetime.fromisoformat(sys.argv[1])
state_dir = Path(sys.argv[2])
state_dir.mkdir(parents=True, exist_ok=True)
repository = MarketplaceRepository(
    "sqlite+pysqlite:///" + str(state_dir / "marketplace.sqlite3")
)
now = expires_at
preview = PurchasePreview(
    preview_id="preview_sqlite",
    offering_id="offering_a",
    user_id="user_a",
    quote_hash="0x" + "aa" * 32,
    input_hash="0x" + "bb" * 32,
    payment={"amount_atomic": "300000"},
    execution_mode="clink_allowance",
    expires_at=expires_at,
    created_at=now,
)
repository.save_preview(preview)
stored = repository.get_preview(preview.preview_id)
assert stored is not None
assert stored.expires_at.tzinfo is None
print(json.dumps(stored.model_dump(mode="json"), sort_keys=True))
repository.engine.dispose()
'''


_CANARY_SCENARIO = r'''
from contextlib import contextmanager
from datetime import UTC
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from examples.monad_commerce.hosted_canary import HostedCanary


OWNER = "0x" + "11" * 20
TOKEN = "0x" + "22" * 20
EXECUTOR = "0x" + "33" * 20
PAYEE = "0x" + "44" * 20


class Gate:
    def __init__(self):
        self.calls = []

    @contextmanager
    def enter(self, *scope):
        self.calls.append(scope)
        yield self


def _canary(payload):
    canary = HostedCanary.__new__(HostedCanary)
    canary.gate = Gate()
    canary.tenant_id, canary.owner = "tenant_a", OWNER
    canary.network = SimpleNamespace(token=TOKEN, executor=EXECUTOR, payee=PAYEE)
    canary._start_market = lambda: None
    calls = []

    def request(method, args):
        calls.append(method)
        if method == "preview_state":
            return payload
        if method == "purchase":
            raise RuntimeError("purchase_not_found")
        if method == "execute":
            return {
                "purchase_id": "purchase_sqlite",
                "preview_id": "preview_sqlite",
                "state": "payment_submitted",
                "settlement": {"verified": False},
            }
        raise AssertionError(method)

    canary.market = SimpleNamespace(request=request)
    return canary, calls


def _exercise(case, payload):
    canary, calls = _canary(payload)
    try:
        canary.request("execute", {"preview_id": "preview_sqlite"})
    except ValueError as exc:
        if case not in {"expired_naive", "invalid"}:
            raise
        assert "expired" in str(exc) or case == "invalid"
        return {
            "error": type(exc).__name__,
            "gate_calls": len(canary.gate.calls),
            "execute_calls": calls.count("execute"),
        }
    if case in {"expired_naive", "invalid"}:
        raise AssertionError("invalid expiry was admitted")
    return {
        "gate_calls": len(canary.gate.calls),
        "execute_calls": calls.count("execute"),
    }


case = sys.argv[1]
payload = json.loads(sys.argv[2])
print(json.dumps(_exercise(case, payload), sort_keys=True))
'''
