"""Hosted first-party catalog freshness checks in an isolated Marketplace child."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _worker_env() -> dict[str, str]:
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG", "LC_ALL")
        if key in os.environ
    }
    env["PYTHONPATH"] = os.pathsep.join(
        (
            str(ROOT / "apps" / "marketplace"),
            str(ROOT / "apps" / "core"),
            str(ROOT / "apps" / "node"),
            str(ROOT),
        )
    )
    return env


def _run_worker(mode: str, state_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _SCENARIO, mode, str(state_dir)],
        cwd=ROOT,
        env=_worker_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


_SCENARIO = r'''
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from types import SimpleNamespace
import threading

import httpx

from agentonomy_commerce.merchant import ReviewMerchant
from agentonomy_commerce.runtime import OFFERING_ID, RESOURCE
from examples.monad_commerce.hosted_market_runtime import HostedCommerceRuntime
from services.marketplace_repository import MarketplaceRepository
from services.quote_service import QuoteService
from shared.models import PaymentOption, Provider, ServiceOffering
from storage.tables import OfferingRow


NETWORK = "eip155:10143"
TOKEN = "0x" + "11" * 20
PAY_TO = "0x" + "22" * 20
PROVIDER_ID = "commerce_analytics"
HEALTH_SECRET = "health-secret-must-not-escape"


def _offering():
    return ServiceOffering(
        offering_id=OFFERING_ID,
        provider_id=PROVIDER_ID,
        source="agentonomy_review",
        source_id="POST " + RESOURCE,
        name="CSV expense reconciliation",
        endpoint=RESOURCE,
        method="POST",
        description="Reconcile transaction CSV and return category totals.",
        input_schema={"type": "object", "required": ["csv_text"]},
        output_schema={"type": "object", "required": ["net_totals"]},
        tags=["csv", "reconciliation"],
        status="verified",
        metadata={
            "trust_tier": "clink_verified",
            "verification_source": "first_party_review_service",
            "settlement_mode": "local_anvil",
        },
        payment_options=[PaymentOption(
            scheme="exact",
            network=NETWORK,
            asset=TOKEN,
            amount_atomic="300000",
            pay_to=PAY_TO,
            price_usd="0.30",
            metadata={"token": "TestUSD", "simulation": False},
        )],
    )


def _runtime(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    repository = MarketplaceRepository(
        f"sqlite+pysqlite:///{root / 'marketplace.sqlite3'}"
    )
    provider = Provider(
        provider_id=PROVIDER_ID,
        name="Agentonomy CSV Reconciliation",
        domain="merchant.agentonomy.invalid",
        source="agentonomy_review",
        status="active",
        metadata={"settlement_mode": "local_anvil", "service_transport": "http"},
    )
    offering = _offering()
    repository.upsert_provider(provider)
    repository.upsert_offering(
        offering,
        verified_at=datetime.now(UTC) - timedelta(hours=2),
    )
    merchant = ReviewMerchant(
        root / "merchant",
        receipt_secret=HEALTH_SECRET,
        network=NETWORK,
        token=TOKEN,
        pay_to=PAY_TO,
        port=0,
        resource=RESOURCE,
    )
    merchant.__enter__()
    runtime = HostedCommerceRuntime.__new__(HostedCommerceRuntime)
    runtime.state_dir = root
    runtime.repository = repository
    runtime.provider = provider
    runtime.offerings = (offering,)
    runtime.quotes = QuoteService(repository, native_provider_ids={PROVIDER_ID})
    runtime.purchases = object()
    runtime._entered = True
    runtime._network = NETWORK
    runtime._asset = TOKEN
    runtime._pay_to = PAY_TO
    runtime.merchant = merchant
    return runtime, merchant, repository


def _verified_at(repository):
    with repository.sessions() as session:
        row = session.get(OfferingRow, OFFERING_ID)
        return row.last_verified_at, dict(row.payload), row.status


class _HealthHandler(BaseHTTPRequestHandler):
    mode = "wrong_scope"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/health" and self.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/health-final")
            self.end_headers()
            return
        if self.path == "/health-final" and self.mode == "redirect":
            self._send_health()
            return
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        self._send_health()

    def _send_health(self):
        body = {
            "status": "ready",
            "merchant_id": PROVIDER_ID,
            "network": NETWORK,
            "token": TOKEN,
            "pay_to": PAY_TO if self.mode == "redirect" else "0x" + "33" * 20,
            "resource": RESOURCE,
        }
        if self.mode == "wrong_scope":
            body["receipt_secret"] = HEALTH_SECRET
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        return


def _fake_health_endpoint(root: Path, mode: str):
    _HealthHandler.mode = mode
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, thread, f"http://127.0.0.1:{port}/v1/reconcile"


def _close(runtime, merchant, repository):
    merchant.__exit__(None, None, None)
    repository.engine.dispose()


def run_stale(root: Path):
    runtime, merchant, repository = _runtime(root)
    try:
        before, payload, status = _verified_at(repository)
        search = runtime.search("")
        details = runtime.details(OFFERING_ID)
        after, refreshed_payload, refreshed_status = _verified_at(repository)
        assert search["count"] == 1
        assert search["items"][0]["offering_id"] == OFFERING_ID
        assert details["offering"]["offering_id"] == OFFERING_ID
        assert len(details["quotes"]) == 1
        assert after > before
        assert refreshed_payload == payload
        assert refreshed_status == status == "verified"
        assert not any(key.startswith("AWS_") for key in os.environ)
        print(json.dumps({"status": "fresh", "count": search["count"]}))
    finally:
        _close(runtime, merchant, repository)


def run_rejected(root: Path):
    for mode in ("redirect", "wrong_scope", "bad_url"):
        runtime, merchant, repository = _runtime(root / mode)
        server = thread = None
        try:
            before, payload, status = _verified_at(repository)
            if mode == "bad_url":
                runtime.merchant = SimpleNamespace(
                    endpoint="http://127.0.0.1:1/v1/reconcile?secret=bad",
                    receipt_resource=RESOURCE,
                )
            else:
                server, thread, endpoint = _fake_health_endpoint(root / mode, mode)
                runtime.merchant = SimpleNamespace(
                    endpoint=endpoint,
                    receipt_resource=RESOURCE,
                )
            for operation in (
                lambda: runtime.search(""),
                lambda: runtime.details(OFFERING_ID),
            ):
                try:
                    operation()
                except RuntimeError as exc:
                    assert str(exc) == "merchant unavailable"
                    assert HEALTH_SECRET not in str(exc)
                else:
                    raise AssertionError(f"{mode} health failure was accepted")
            after, rejected_payload, rejected_status = _verified_at(repository)
            assert after == before
            assert rejected_payload == payload
            assert rejected_status == status == "verified"
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            _close(runtime, merchant, repository)
    runtime, merchant, repository = _runtime(root / "tampered")
    try:
        before, payload, status = _verified_at(repository)
        with repository.sessions.begin() as session:
            row = session.get(OfferingRow, OFFERING_ID)
            row.payload = {**row.payload, "endpoint": RESOURCE + "/tampered"}
        try:
            runtime.search("")
        except RuntimeError as exc:
            assert str(exc) == "merchant unavailable"
        else:
            raise AssertionError("tampered persisted offering was accepted")
        after, rejected_payload, rejected_status = _verified_at(repository)
        assert after == before
        assert rejected_payload != payload
        assert rejected_payload["endpoint"] == RESOURCE + "/tampered"
        assert rejected_status == status == "verified"
    finally:
        _close(runtime, merchant, repository)
    print(json.dumps({"status": "fail_closed"}))


mode = os.sys.argv[1]
root = Path(os.sys.argv[2])
root.mkdir(parents=True, exist_ok=True)
if mode == "stale":
    run_stale(root)
elif mode == "rejected":
    run_rejected(root)
else:
    raise SystemExit(f"unknown mode: {mode}")
'''


def test_hosted_catalog_refreshes_first_party_health_after_one_hour(tmp_path: Path):
    result = _run_worker("stale", tmp_path / "stale")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "fresh", "count": 1}


def test_hosted_catalog_health_failures_do_not_refresh_or_admit_service(tmp_path: Path):
    result = _run_worker("rejected", tmp_path / "rejected")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "fail_closed"}
