"""Local Anvil acceptance tests for payment and delivery recovery.

These tests exercise the real Monad budget composition through its process
boundaries.  They deliberately use a loopback Anvil chain and the in-repo
TestUSD/executor artifacts; no public RPC, production endpoint, or simulated
settlement path is involved.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any

import httpx
from eth_utils import keccak

from examples.commerce.node import MarketplaceBridge
from examples.monad_commerce.local_chain import LocalChain


CSV = (
    "transaction_id,date,description,amount,currency,category\n"
    "1,2026-10-03,Sale,10.00,USD,sales\n"
    "2,2026-10-03,Fee,-2.00,USD,fees\n"
)
PAYMENT_EXECUTED_TOPIC = "0x" + keccak(
    text="PaymentExecuted(bytes32,bytes32,address,bytes32,bytes32,address,address,uint256)"
).hex()


def _bootstrap(chain: LocalChain, *, rpc_urls: tuple[str, str] | None = None) -> dict[str, Any]:
    return {
        "mode": "local_anvil",
        "chain_id": 31337,
        "rpc_urls": list(rpc_urls or (chain.url, chain.url)),
        "token": chain.token,
        "executor": chain.executor,
        "payee": chain.payee,
        "owner_key": "0x" + chain.owner.key.hex(),
        "execution_key": "0x" + chain.execution_signer.key.hex(),
        "relayer_key": "0x" + chain.relayer.key.hex(),
        "approval_tx": chain.approval_tx,
    }


def _bridge(state_dir: Path, chain: LocalChain, *, rpc_urls: tuple[str, str] | None = None) -> MarketplaceBridge:
    bridge = MarketplaceBridge(
        state_dir,
        worker_module="examples.monad_commerce.market_worker",
    )
    bridge.request("initialize", _bootstrap(chain, rpc_urls=rpc_urls))
    return bridge


def _rpc(url: str, method: str, params: list[Any]) -> Any:
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        response.raise_for_status()
        payload = response.json()
    if "error" in payload or payload.get("id") != 1 or "result" not in payload:
        raise AssertionError(f"invalid local RPC response for {method}: {payload!r}")
    return payload["result"]


def _payment_events(chain: LocalChain, from_block: int) -> list[dict[str, Any]]:
    return _rpc(
        chain.url,
        "eth_getLogs",
        [
            {
                "address": chain.executor,
                "fromBlock": hex(from_block),
                "toBlock": "latest",
                "topics": [PAYMENT_EXECUTED_TOPIC],
            }
        ],
    )


def _latest_block(chain: LocalChain) -> int:
    return int(_rpc(chain.url, "eth_blockNumber", []), 16)


class _RpcMirror:
    """Loopback JSON-RPC mirror used to make one real observation disagree."""

    def __init__(self, upstream: str) -> None:
        self.upstream = upstream
        self.mutate_receipt_status = True
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 2_000_000:
                        raise ValueError("invalid JSON-RPC body length")
                    request = json.loads(self.rfile.read(length))
                    with httpx.Client(timeout=10, trust_env=False) as client:
                        response = client.post(
                            owner.upstream,
                            json=request,
                        )
                        response.raise_for_status()
                        payload = response.json()
                    if (
                        owner.mutate_receipt_status
                        and request.get("method") == "eth_getTransactionReceipt"
                        and isinstance(payload.get("result"), dict)
                    ):
                        result = dict(payload["result"])
                        result["status"] = "0x0"
                        payload = {**payload, "result": result}
                    body = json.dumps(payload, separators=(",", ":")).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception:
                    body = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32000, "message": "mirror failure"},
                        }
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="monad-test-rpc-mirror",
            daemon=True,
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> "_RpcMirror":
        self.thread.start()
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class _JsonLineClient:
    """Minimal client for the delivery-failure test worker."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.request_id = 0

    def request(self, method: str, arguments: dict[str, Any] | None = None) -> Any:
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("worker pipes are unavailable")
        self.request_id += 1
        self.process.stdin.write(
            json.dumps(
                {"id": self.request_id, "method": method, "arguments": arguments or {}}
            )
            + "\n"
        )
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("delivery-failure worker stopped before replying")
        response = json.loads(line)
        if response.get("id") != self.request_id:
            raise RuntimeError(f"delivery-failure worker response mismatch: {response!r}")
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]

    def close(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


_DELIVERY_FAILURE_WORKER = r'''
from __future__ import annotations

import json
from pathlib import Path
import socket
import sys

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "apps" / "marketplace"))
sys.path.insert(0, str(root))

from examples.monad_commerce.merchant import BudgetMerchant
from examples.monad_commerce.runtime import BudgetCommerceRuntime


original_handle_post = BudgetMerchant._handle_post


def fail_first_delivery(self, handler):
    global remaining_failures
    if remaining_failures:
        remaining_failures -= 1
        handler._send(503, {"error": "test-only merchant delivery failure"})
        return
    return original_handle_post(self, handler)


# Test-only merchant HTTP failure. Core, RPC and chain execution remain real.
BudgetMerchant._handle_post = fail_first_delivery

bootstrap = json.loads(sys.stdin.readline(65537))
state_dir = Path(sys.argv[2]).resolve()
failure_marker = state_dir / ".delivery-failure-injected"
remaining_failures = 0 if failure_marker.exists() else 1
if remaining_failures:
    failure_marker.write_text("injected", encoding="utf-8")
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    merchant_port = listener.getsockname()[1]
runtime = BudgetCommerceRuntime(state_dir, merchant_port, bootstrap)
runtime.__enter__()
print(json.dumps({"ready": True}), flush=True)
try:
    for line in sys.stdin:
        if len(line) > 1_048_576:
            break
        request = json.loads(line)
        try:
            method = request["method"]
            result = getattr(runtime, method)(**request.get("arguments", {}))
            response = {"id": request["id"], "result": result}
        except Exception as exc:
            response = {"id": request.get("id"), "error": str(exc)}
        print(json.dumps(response, default=str), flush=True)
finally:
    runtime.__exit__(None, None, None)
'''


def _delivery_failure_worker(root: Path, state_dir: Path, bootstrap: dict[str, Any]) -> _JsonLineClient:
    process = subprocess.Popen(
        [sys.executable, "-c", _DELIVERY_FAILURE_WORKER, str(root), str(state_dir)],
        cwd=root,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": f"{root}:{root / 'apps' / 'facilitator'}:{root / 'apps' / 'core'}:{root / 'apps' / 'node'}",
            "PYTHONUNBUFFERED": "1",
        },
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps(bootstrap) + "\n")
    process.stdin.flush()
    ready = process.stdout.readline()
    if not ready or json.loads(ready).get("ready") is not True:
        process.terminate()
        process.wait(timeout=5)
        raise RuntimeError(f"delivery-failure worker failed to start: {ready!r}")
    return _JsonLineClient(process)


def test_broadcast_then_marketplace_bridge_restart_reuses_purchase_and_tx(tmp_path: Path) -> None:
    with LocalChain() as chain:
        chain.deploy()
        chain.approval_tx = chain.approve(1_000_000)["transactionHash"]
        chain.mine(5)
        baseline_block = _latest_block(chain)
        bridge = _bridge(tmp_path, chain)
        try:
            preview = bridge.request(
                "preview",
                {
                    "offering_id": "csv-reconciliation-v1",
                    "csv_text": CSV,
                    "idempotency_key": "restart-before-verification",
                },
            )
            first = bridge.request("execute", {"preview_id": preview["preview_id"]})
            assert first["state"] == "payment_submitted", first
            purchase_id = first["purchase_id"]
            tx_hash = first["settlement"]["transaction_hash"]
            assert tx_hash.startswith("0x") and len(tx_hash) == 66
        finally:
            bridge.close()

        chain.mine(5)
        restarted = _bridge(tmp_path, chain)
        try:
            recovered = restarted.request(
                "execute", {"preview_id": preview["preview_id"]}
            )
            assert recovered["state"] == "delivered", recovered
            assert recovered["purchase_id"] == purchase_id
            assert recovered["settlement"]["transaction_hash"] == tx_hash
            snapshot = restarted.request("snapshot")
            assert snapshot["used_amount_usdc"] == "0.30"
            assert snapshot["reserved_amount_usdc"] == "0.00"
            assert snapshot["settlement_submissions"] == 1
        finally:
            restarted.close()

        events = _payment_events(chain, baseline_block + 1)
        assert len(events) == 1


def test_three_real_point_three_payments_then_fourth_is_budget_refused(tmp_path: Path) -> None:
    with LocalChain() as chain:
        chain.deploy()
        chain.approval_tx = chain.approve(1_000_000)["transactionHash"]
        chain.mine(5)
        baseline_block = _latest_block(chain)
        bridge = _bridge(tmp_path, chain)
        try:
            delivered: list[dict[str, Any]] = []
            for index in range(3):
                preview = bridge.request(
                    "preview",
                    {
                        "offering_id": "csv-reconciliation-v1",
                        "csv_text": CSV,
                        "idempotency_key": f"budget-{index}",
                    },
                )
                result = bridge.request("execute", {"preview_id": preview["preview_id"]})
                if result["state"] == "payment_submitted":
                    chain.mine(5)
                    result = bridge.request(
                        "execute", {"preview_id": preview["preview_id"]}
                    )
                assert result["state"] == "delivered", result
                delivered.append(result)

            before_fourth = bridge.request("snapshot")
            fourth_preview = bridge.request(
                "preview",
                {
                    "offering_id": "csv-reconciliation-v1",
                    "csv_text": CSV,
                    "idempotency_key": "budget-fourth",
                },
            )
            blocked = bridge.request(
                "execute", {"preview_id": fourth_preview["preview_id"]}
            )
            after_fourth = bridge.request("snapshot")
        finally:
            bridge.close()

        assert len({item["purchase_id"] for item in delivered}) == 3
        assert blocked["state"] == "confirmation_required", blocked
        assert blocked["reason_code"] == "BUDGET_EXCEEDED"
        assert before_fourth["used_amount_usdc"] == after_fourth["used_amount_usdc"] == "0.90"
        assert after_fourth["reserved_amount_usdc"] == "0.00"
        assert before_fourth["settlement_submissions"] == after_fourth["settlement_submissions"] == 3
        assert len(_payment_events(chain, baseline_block + 1)) == 3


def test_paid_http_delivery_failure_recovers_original_purchase_without_second_payment(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    with LocalChain() as chain:
        chain.deploy()
        chain.approval_tx = chain.approve(1_000_000)["transactionHash"]
        chain.mine(5)
        baseline_block = _latest_block(chain)
        worker = _delivery_failure_worker(root, tmp_path, _bootstrap(chain))
        try:
            preview = worker.request(
                "preview",
                {
                    "offering_id": "csv-reconciliation-v1",
                    "csv_text": CSV,
                    "idempotency_key": "delivery-recovery",
                },
            )
            first = worker.request("execute", {"preview_id": preview["preview_id"]})
            if first["state"] == "payment_submitted":
                chain.mine(5)
                first = worker.request(
                    "execute", {"preview_id": preview["preview_id"]}
                )
            assert first["state"] == "paid_but_undelivered", first
            purchase_id = first["purchase_id"]
            tx_hash = first["settlement"]["transaction_hash"]
            assert first["settlement"]["verified"] is True
        finally:
            # Restart both Marketplace and its isolated Core bridge after the
            # paid marker is persisted.  The worker's marker file makes the
            # injected HTTP failure happen only once across these processes.
            worker.close()

        worker = _delivery_failure_worker(root, tmp_path, _bootstrap(chain))
        try:
            recovered = worker.request(
                "execute", {"preview_id": preview["preview_id"]}
            )
            after = worker.request("snapshot")
        finally:
            worker.close()

        assert recovered["state"] == "delivered", recovered
        assert recovered["purchase_id"] == purchase_id
        assert recovered["settlement"]["transaction_hash"] == tx_hash
        assert after["used_amount_usdc"] == "0.30"
        assert after["reserved_amount_usdc"] == "0.00"
        assert after["settlement_submissions"] == 1
        assert after["merchant_deliveries"] == 1
        assert len(_payment_events(chain, baseline_block + 1)) == 1


def test_rpc_disagreement_keeps_real_reservation_until_same_tx_verifies(tmp_path: Path) -> None:
    with LocalChain() as chain:
        chain.deploy()
        chain.approval_tx = chain.approve(1_000_000)["transactionHash"]
        chain.mine(5)
        baseline_block = _latest_block(chain)
        with _RpcMirror(chain.url) as mirror:
            bridge = _bridge(tmp_path, chain, rpc_urls=(chain.url, mirror.url))
            try:
                preview = bridge.request(
                    "preview",
                    {
                        "offering_id": "csv-reconciliation-v1",
                        "csv_text": CSV,
                        "idempotency_key": "rpc-disagreement",
                    },
                )
                pending = bridge.request(
                    "execute", {"preview_id": preview["preview_id"]}
                )
                assert pending["state"] == "payment_submitted", pending
                tx_hash = pending["settlement"]["transaction_hash"]
                chain.mine(5)
                still_pending = bridge.request(
                    "execute", {"preview_id": preview["preview_id"]}
                )
                assert still_pending["state"] == "payment_submitted", still_pending
                assert still_pending["settlement"]["transaction_hash"] == tx_hash
                pending_snapshot = bridge.request("snapshot")
                assert pending_snapshot["used_amount_usdc"] == "0.00"
                assert pending_snapshot["reserved_amount_usdc"] == "0.30"
                assert pending_snapshot["settlement_submissions"] == 1

                mirror.mutate_receipt_status = False
                chain.mine(5)
                recovered = bridge.request(
                    "execute", {"preview_id": preview["preview_id"]}
                )
                final_snapshot = bridge.request("snapshot")
            finally:
                bridge.close()

        assert recovered["state"] == "delivered", recovered
        assert recovered["settlement"]["transaction_hash"] == tx_hash
        assert final_snapshot["used_amount_usdc"] == "0.30"
        assert final_snapshot["reserved_amount_usdc"] == "0.00"
        assert final_snapshot["settlement_submissions"] == 1
        assert len(_payment_events(chain, baseline_block + 1)) == 1
