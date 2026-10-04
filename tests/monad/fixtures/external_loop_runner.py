"""Subprocess runner for the external-wallet Anvil integration cases.

The Core and Marketplace applications both expose a top-level ``services``
package. Running these cases in a clean interpreter prevents another Monad
test's already-imported package from selecting the wrong application namespace.
"""

from __future__ import annotations

from pathlib import Path
import sys

from external_wallet import ExternalWalletHarness


CSV = (
    "transaction_id,date,description,amount,currency,category\n"
    "1,2026-10-03,Sale,10.00,USD,sales\n"
    "2,2026-10-03,Fee,-2.00,USD,fees\n"
)


def _complete_payment(harness: ExternalWalletHarness) -> tuple[dict, dict]:
    preview = harness.runtime.preview(
        "csv-reconciliation-v1",
        CSV,
        "external-wallet-purchase-1",
    )
    submitted = harness.runtime.execute(preview["preview_id"])
    assert submitted["state"] == "payment_submitted"
    harness.chain.mine(5)
    delivered = harness.runtime.execute(preview["preview_id"])
    assert delivered["state"] == "delivered", delivered
    assert delivered["service_result"]["settlement_mode"] == "local_anvil"
    assert delivered["settlement"]["verified"] is True
    assert delivered["settlement"]["amount_atomic"] == "300000"
    return preview, delivered


def _preview(harness: ExternalWalletHarness, idempotency_key: str) -> dict:
    return harness.runtime.preview(
        "csv-reconciliation-v1",
        CSV,
        idempotency_key,
    )


def run_delivers_and_replays_after_restart_without_new_gas_payment(
    tmp_path: Path,
) -> None:
    with ExternalWalletHarness(tmp_path) as harness:
        preview, delivered = _complete_payment(harness)
        first_relayer_nonce = harness.relayer_latest_nonce()
        assert first_relayer_nonce == 1

        harness.restart_runtime()
        replay = harness.runtime.execute(preview["preview_id"])
        assert replay["purchase_id"] == delivered["purchase_id"]
        assert replay["state"] == "delivered"
        assert replay["service_result"] == delivered["service_result"]
        assert harness.relayer_latest_nonce() == first_relayer_nonce
        harness.assert_private_keys_never_persisted()


def run_core_revoke_keeps_previous_payment_readable_and_reconcilable(
    tmp_path: Path,
) -> None:
    with ExternalWalletHarness(tmp_path) as harness:
        _preview, delivered = _complete_payment(harness)
        purchase = harness.runtime.repository.get_purchase(delivered["purchase_id"])
        assert purchase is not None and purchase.reservation_id

        revoked = harness.runtime.revoke()
        assert revoked["status"] == "revoked"

        read = harness.runtime.purchase(delivered["purchase_id"])
        assert read["state"] == "delivered"
        assert read["purchase_id"] == delivered["purchase_id"]
        assert read["settlement"]["verified"] is True

        reconciled = harness.runtime.core.reconcile(purchase.reservation_id)
        assert reconciled["state"] in {"settled", "finalized"}
        assert reconciled["tx_hash"] == delivered["settlement"]["transaction_hash"]
        assert harness.relayer_latest_nonce() == 1
        harness.assert_private_keys_never_persisted()


def run_payment_submitted_restart_recovers_by_purchase_id(tmp_path: Path) -> None:
    with ExternalWalletHarness(tmp_path) as harness:
        preview = _preview(harness, "external-wallet-recovery-payment-submitted")
        submitted = harness.runtime.execute(preview["preview_id"])
        assert submitted["state"] == "payment_submitted"
        purchase_id = submitted["purchase_id"]
        first_relayer_nonce = harness.relayer_latest_nonce()
        assert first_relayer_nonce == 1

        harness.restart_runtime(fail_next_delivery=False)
        harness.chain.mine(5)
        recovered = harness.runtime.recover_purchase(purchase_id)

        assert recovered["purchase_id"] == purchase_id
        assert recovered["state"] == "delivered", recovered
        assert recovered["service_result"]["settlement_mode"] == "local_anvil"
        assert recovered["settlement"]["verified"] is True
        assert harness.runtime.repository.get_preview(preview["preview_id"]) is not None
        assert harness.relayer_latest_nonce() == first_relayer_nonce
        harness.assert_private_keys_never_persisted()


def run_paid_delivery_failure_restarts_and_recovers_same_order(
    tmp_path: Path,
) -> None:
    with ExternalWalletHarness(tmp_path, fail_next_delivery=True) as harness:
        preview = _preview(harness, "external-wallet-recovery-paid-delivery")
        submitted = harness.runtime.execute(preview["preview_id"])
        assert submitted["state"] == "payment_submitted"
        harness.chain.mine(5)
        failed = harness.runtime.execute(preview["preview_id"])

        assert failed["state"] == "paid_but_undelivered", failed
        purchase_id = failed["purchase_id"]
        first_relayer_nonce = harness.relayer_latest_nonce()
        assert first_relayer_nonce == 1

        revoked = harness.runtime.revoke()
        assert revoked["status"] == "revoked"
        harness.restart_runtime(fail_next_delivery=False)
        recovered = harness.runtime.recover_purchase(purchase_id)

        assert recovered["purchase_id"] == purchase_id
        assert recovered["state"] == "delivered", recovered
        assert recovered["service_result"]["settlement_mode"] == "local_anvil"
        assert recovered["settlement"]["verified"] is True
        assert harness.relayer_latest_nonce() == first_relayer_nonce
        assert harness.runtime.purchase(purchase_id)["state"] == "delivered"
        harness.assert_private_keys_never_persisted()


CASES = {
    "delivers_and_replays_after_restart_without_new_gas_payment":
        run_delivers_and_replays_after_restart_without_new_gas_payment,
    "core_revoke_keeps_previous_payment_readable_and_reconcilable":
        run_core_revoke_keeps_previous_payment_readable_and_reconcilable,
    "payment_submitted_restart_recovers_by_purchase_id":
        run_payment_submitted_restart_recovers_by_purchase_id,
    "paid_delivery_failure_restarts_and_recovers_same_order":
        run_paid_delivery_failure_restarts_and_recovers_same_order,
}


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in CASES:
        print("usage: external_loop_runner.py CASE TMP_PATH", file=sys.stderr)
        return 2
    CASES[argv[1]](Path(argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
