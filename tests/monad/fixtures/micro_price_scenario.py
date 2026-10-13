"""Local-Anvil micro-price loops through the external-wallet composition.

The existing external-wallet fixture intentionally uses the legacy 0.30
signing scope.  These scenarios subclass that fixture and inject the selected
service amount only into the test child signer and Marketplace composition.
Core, the BudgetExecutor contract, the RPC watcher, and the HTTP merchant are
otherwise the existing real local components.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from eth_utils import keccak

from external_wallet import (
    ExternalCommerceRuntime,
    ExternalCoreBridge,
    ExternalWalletHarness,
    ROOT,
)


CSV = (
    "transaction_id,date,description,amount,currency,category\n"
    "1,2026-10-03,Sale,10.00,USD,sales\n"
    "2,2026-10-03,Fee,-2.00,USD,fees\n"
)

_LEGACY_AMOUNT_ATOMIC = 300_000


# Run the existing clean external Core worker, replacing only the test
# worker's SigningScope constructor with the selected amount.  The production
# worker and Core protocol remain unchanged; no private key is persisted.
_MICRO_CORE_WORKER = r'''
import sys

import external_core_worker as worker


service_amount_atomic = int(sys.argv[2])
scope_type = worker.SigningScope


def configured_scope(**kwargs):
    return scope_type(**kwargs, service_amount_atomic=service_amount_atomic)


worker.SigningScope = configured_scope
sys.argv = [sys.argv[0], sys.argv[1]]
raise SystemExit(worker.main())
'''


class MicroPriceExternalCoreBridge(ExternalCoreBridge):
    """External Core bridge with a test-only signed service-price scope."""

    def __init__(
        self,
        state_dir: Path,
        deployment: dict[str, object],
        execution_private_key: str,
        gas_private_key: str,
        service_amount_atomic: int,
    ) -> None:
        super().__init__(state_dir, deployment, execution_private_key, gas_private_key)
        self.service_amount_atomic = service_amount_atomic

    def _start(self) -> None:
        if self._broken is not None:
            raise RuntimeError("external Core channel is unavailable")
        if self.process is not None:
            return
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "SYSTEMROOT", "TMPDIR", "LANG", "LC_ALL"}
        }
        env["PYTHONPATH"] = os.pathsep.join(
            (
                str(ROOT / "apps" / "marketplace"),
                str(ROOT),
                str(ROOT / "apps" / "core"),
                str(Path(__file__).resolve().parent),
            )
        )
        env["PYTHONUNBUFFERED"] = "1"
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _MICRO_CORE_WORKER,
                str(self.state_dir),
                str(self.service_amount_atomic),
            ],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            bufsize=1,
        )
        startup = {
            "deployment": self.bootstrap,
            "execution_private_key": self._execution_private_key,
            "gas_private_key": self._gas_private_key,
        }
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(startup, separators=(",", ":")) + "\n")
        self.process.stdin.flush()


class MicroPriceCommerceRuntime(ExternalCommerceRuntime):
    """Existing Marketplace/merchant runtime with a test-only current price."""

    def __init__(
        self,
        state_dir: Path,
        merchant_port: int,
        deployment: dict[str, object],
        execution_private_key: str,
        gas_private_key: str,
        service_amount_atomic: int,
        fail_next_delivery: bool = False,
    ) -> None:
        super().__init__(
            state_dir,
            merchant_port,
            deployment,
            execution_private_key,
            gas_private_key,
            fail_next_delivery,
        )
        self._configure_service_price(
            {"signer_configuration": {"scope": {"service_amount_atomic": service_amount_atomic}}}
        )
        self.core = MicroPriceExternalCoreBridge(
            state_dir,
            deployment,
            execution_private_key,
            gas_private_key,
            service_amount_atomic,
        )


class MicroPriceHarness(ExternalWalletHarness):
    """Reuse the external-wallet Anvil lifecycle with a selected price."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        service_amount_atomic: int,
        fail_next_delivery: bool = False,
    ) -> None:
        super().__init__(tmp_path, fail_next_delivery=fail_next_delivery)
        self.service_amount_atomic = service_amount_atomic

    def _start_runtime(self) -> None:
        assert self.deployment is not None
        self.runtime = MicroPriceCommerceRuntime(
            self.state_dir,
            self._free_port(),
            self.deployment,
            self._execution_private_key,
            self._gas_private_key,
            self.service_amount_atomic,
            self.fail_next_delivery,
        )
        self.runtime.__enter__()

    def restart_runtime(
        self,
        *,
        fail_next_delivery: bool | None = None,
        service_amount_atomic: int | None = None,
    ) -> None:
        if self.runtime is not None:
            self.runtime.__exit__(None, None, None)
            self.runtime = None
        if fail_next_delivery is not None:
            self.fail_next_delivery = fail_next_delivery
        if service_amount_atomic is not None:
            self.service_amount_atomic = service_amount_atomic
        self._start_runtime()


def _token_balance(harness: MicroPriceHarness, owner: str) -> int:
    assert harness.chain is not None
    data = "0x" + (
        keccak(text="balanceOf(address)")[:4]
        + bytes.fromhex(owner[2:]).rjust(32, b"\0")
    ).hex()
    return int(
        harness.chain.rpc.call(
            "eth_call",
            [{"to": harness.chain.token, "data": data}, "latest"],
        ),
        16,
    )


def _assert_snapshot(runtime, *, used: str, remaining: str) -> None:
    snapshot = runtime.snapshot()
    assert snapshot["budget_usdc"] == "1.00"
    assert snapshot["used_amount_usdc"] == used
    assert snapshot["reserved_amount_usdc"] == "0.00"
    assert snapshot["remaining_amount_usdc"] == remaining


def _pay_and_deliver(harness: MicroPriceHarness, amount_atomic: int, price_usd: str, key: str):
    assert harness.runtime is not None and harness.chain is not None
    catalog = harness.runtime.search("csv")
    assert catalog["count"] == 1, catalog
    item = catalog["items"][0]
    assert item["price_usd"] == price_usd, catalog
    assert item["payment"]["price_usd"] == price_usd, item
    assert item["payment"]["amount_atomic"] == str(amount_atomic), item

    details = harness.runtime.details(item["offering_id"])
    assert details["quotes"], details
    quote_payment = details["quotes"][0]["payment"]
    assert quote_payment["price_usd"] == price_usd, details
    assert quote_payment["amount_atomic"] == str(amount_atomic), details

    preview = harness.runtime.preview("csv-reconciliation-v1", CSV, key)
    assert preview["payment"]["amount_atomic"] == str(amount_atomic), preview
    assert preview["payment"]["price_usd"] == price_usd, preview

    submitted = harness.runtime.execute(preview["preview_id"])
    assert submitted["state"] == "payment_submitted", submitted
    harness.chain.mine(5)
    delivered = harness.runtime.execute(preview["preview_id"])
    assert delivered["state"] == "delivered", delivered
    assert delivered["settlement"]["verified"] is True
    assert delivered["settlement"]["amount_atomic"] == str(amount_atomic)
    assert delivered["service_result"]["settlement_mode"] == "local_anvil"
    return preview, delivered


def run_exact_delivery_replay(
    tmp_path: Path,
    *,
    amount_atomic: int,
    price_usd: str,
    used: str,
    remaining: str,
) -> None:
    with MicroPriceHarness(tmp_path, service_amount_atomic=amount_atomic) as harness:
        assert harness.chain is not None and harness.runtime is not None
        initial_owner = _token_balance(harness, harness.chain.owner.address)
        initial_payee = _token_balance(harness, harness.chain.payee)
        preview, delivered = _pay_and_deliver(
            harness, amount_atomic, price_usd, "micro-price-order"
        )
        first_tx = delivered["settlement"]["transaction_hash"]
        assert harness.relayer_latest_nonce() == 1
        assert _token_balance(harness, harness.chain.owner.address) == initial_owner - amount_atomic
        assert _token_balance(harness, harness.chain.payee) == initial_payee + amount_atomic
        _assert_snapshot(harness.runtime, used=used, remaining=remaining)

        harness.restart_runtime()
        assert harness.runtime is not None
        replay = harness.runtime.execute(preview["preview_id"])
        assert replay["purchase_id"] == delivered["purchase_id"]
        assert replay["state"] == "delivered"
        assert replay["settlement"]["transaction_hash"] == first_tx
        assert replay["settlement"]["amount_atomic"] == str(amount_atomic)
        assert harness.relayer_latest_nonce() == 1
        assert _token_balance(harness, harness.chain.owner.address) == initial_owner - amount_atomic
        assert _token_balance(harness, harness.chain.payee) == initial_payee + amount_atomic
        _assert_snapshot(harness.runtime, used=used, remaining=remaining)
        harness.assert_private_keys_never_persisted()


def run_exact_001_delivery_replay(tmp_path: Path) -> None:
    run_exact_delivery_replay(
        tmp_path,
        amount_atomic=1_000,
        price_usd="0.001",
        used="0.001",
        remaining="0.999",
    )


def run_exact_0002_delivery_replay(tmp_path: Path) -> None:
    run_exact_delivery_replay(
        tmp_path,
        amount_atomic=200,
        price_usd="0.0002",
        used="0.0002",
        remaining="0.9998",
    )


def run_legacy_030_recovery_after_price_change(tmp_path: Path) -> None:
    with MicroPriceHarness(
        tmp_path,
        service_amount_atomic=_LEGACY_AMOUNT_ATOMIC,
        fail_next_delivery=True,
    ) as harness:
        assert harness.chain is not None and harness.runtime is not None
        initial_owner = _token_balance(harness, harness.chain.owner.address)
        initial_payee = _token_balance(harness, harness.chain.payee)
        preview = harness.runtime.preview(
            "csv-reconciliation-v1", CSV, "legacy-price-recovery"
        )
        assert preview["payment"]["amount_atomic"] == str(_LEGACY_AMOUNT_ATOMIC)
        assert preview["payment"]["price_usd"] == "0.30"
        first = harness.runtime.execute(preview["preview_id"])
        assert first["state"] == "payment_submitted", first
        harness.chain.mine(5)
        failed = harness.runtime.execute(preview["preview_id"])
        assert failed["state"] == "paid_but_undelivered", failed
        purchase_id = failed["purchase_id"]
        first_tx = failed["settlement"]["transaction_hash"]
        assert failed["settlement"]["amount_atomic"] == str(_LEGACY_AMOUNT_ATOMIC)
        assert harness.relayer_latest_nonce() == 1
        assert (
            _token_balance(harness, harness.chain.owner.address)
            == initial_owner - _LEGACY_AMOUNT_ATOMIC
        )
        assert _token_balance(harness, harness.chain.payee) == initial_payee + _LEGACY_AMOUNT_ATOMIC
        _assert_snapshot(harness.runtime, used="0.30", remaining="0.70")

        harness.restart_runtime(
            fail_next_delivery=False,
            service_amount_atomic=1_000,
        )
        assert harness.runtime is not None
        current_payment = harness.runtime.offerings[0].payment_options[0]
        assert current_payment.amount_atomic == "1000"
        assert str(current_payment.price_usd) == "0.001"
        frozen_preview = harness.runtime.repository.get_preview(preview["preview_id"])
        assert frozen_preview is not None
        assert frozen_preview.payment["amount_atomic"] == str(_LEGACY_AMOUNT_ATOMIC)
        assert str(frozen_preview.payment["price_usd"]) == "0.30"

        recovered = harness.runtime.recover_purchase(purchase_id)
        assert recovered["purchase_id"] == purchase_id
        assert recovered["state"] == "delivered", recovered
        assert recovered["settlement"]["transaction_hash"] == first_tx
        assert recovered["settlement"]["amount_atomic"] == str(_LEGACY_AMOUNT_ATOMIC)
        assert harness.relayer_latest_nonce() == 1
        assert (
            _token_balance(harness, harness.chain.owner.address)
            == initial_owner - _LEGACY_AMOUNT_ATOMIC
        )
        assert _token_balance(harness, harness.chain.payee) == initial_payee + _LEGACY_AMOUNT_ATOMIC
        _assert_snapshot(harness.runtime, used="0.30", remaining="0.70")
        harness.assert_private_keys_never_persisted()


CASES = {
    "exact_001_delivery_replay": run_exact_001_delivery_replay,
    "exact_0002_delivery_replay": run_exact_0002_delivery_replay,
    "legacy_030_recovery_after_price_change": run_legacy_030_recovery_after_price_change,
}


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in CASES:
        print("usage: micro_price_scenario.py CASE TMP_PATH", file=sys.stderr)
        return 2
    CASES[argv[1]](Path(argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
