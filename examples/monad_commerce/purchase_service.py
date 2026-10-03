"""Monad-local purchase recovery over the shared Marketplace service.

The common Marketplace service treats ``paid_but_undelivered`` as a terminal
outcome for its other rails.  The local budget rail keeps the paid receipt and
the original service input available for a delivery retry.  This module only
changes that Monad composition; the shared service remains unchanged.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime

from services.purchase_service import PurchaseService, digest


class MonadPurchaseService(PurchaseService):
    """Recover paid local-budget purchases without submitting another payment."""

    _PAID_DELIVERY_STATE = "paid_but_undelivered"
    _ALLOWANCE_MODE = "clink_allowance"
    _PAID_INPUT_TTL_SECONDS = 24 * 60 * 60

    def execute(
        self,
        preview_id,
        *,
        user_confirmed=False,
        spending_authorization_id=None,
        transaction_hash=None,
        payment_response=None,
        opc_installation_id=None,
    ):
        """Retry delivery for a paid Monad purchase before shared execution.

        External proof submissions and every other state continue through the
        shared Marketplace implementation.  A paid local-budget purchase is
        claimed directly, then reconciled/read from Core before the merchant
        is called; no quote, reservation, or settlement operation is repeated.
        """

        preview = self.repository.get_preview(preview_id)
        if preview is None:
            return super().execute(
                preview_id,
                user_confirmed=user_confirmed,
                spending_authorization_id=spending_authorization_id,
                transaction_hash=transaction_hash,
                payment_response=payment_response,
                opc_installation_id=opc_installation_id,
            )
        if preview.opc_installation_id != self._opc_installation_id(
            opc_installation_id
        ):
            raise ValueError("OPC installation mismatch")

        # A caller supplying external proof must retain the shared external
        # payment handling and its validation rules.
        if transaction_hash is not None or payment_response is not None:
            return super().execute(
                preview_id,
                user_confirmed=user_confirmed,
                spending_authorization_id=spending_authorization_id,
                transaction_hash=transaction_hash,
                payment_response=payment_response,
                opc_installation_id=opc_installation_id,
            )

        purchase_id = "purchase_" + preview_id.removeprefix("preview_")
        existing = self.repository.get_purchase(purchase_id)
        if (
            existing
            and existing.execution_mode == self._ALLOWANCE_MODE
            and (
                existing.state == self._PAID_DELIVERY_STATE
                or (
                    existing.state == "payment_submitted"
                    and existing.metadata.get("budget_paid_delivery") is True
                )
            )
        ):
            return self._retry_paid_delivery(existing, preview)

        return super().execute(
            preview_id,
            user_confirmed=user_confirmed,
            spending_authorization_id=spending_authorization_id,
            transaction_hash=transaction_hash,
            payment_response=payment_response,
            opc_installation_id=opc_installation_id,
        )

    def _retry_paid_delivery(self, purchase, preview):
        """Deliver an already-paid purchase using its existing Core receipt."""

        if purchase.input_hash != preview.input_hash:
            return self._paid_delivery_pending(
                purchase,
                reason="INPUT_HASH_MISMATCH",
            )
        if not purchase.reservation_id:
            return self._paid_delivery_pending(
                purchase,
                reason="PAYMENT_RECEIPT_UNAVAILABLE",
            )

        offering = self.repository.active_offering(purchase.offering_id)
        if not offering:
            return self._paid_delivery_pending(
                purchase,
                reason="ACTIVE_PROVIDER_REQUIRED",
            )

        # Claim the paid marker itself.  The claim is released by every
        # pending path so a process restart can safely retry the same purchase.
        claimed, claim_token = self.repository.claim_purchase_execution(
            purchase,
            allowed_states={
                self._PAID_DELIVERY_STATE,
                "payment_submitted",
            },
            expected_execution_mode=self._ALLOWANCE_MODE,
        )
        if not claim_token:
            return self._response(claimed or purchase)
        purchase = claimed

        if purchase.state == self._PAID_DELIVERY_STATE:
            purchase = purchase.model_copy(
                update={
                    "state": "payment_submitted",
                    "reason_code": "DELIVERY_RETRY_IN_PROGRESS",
                    "metadata": self._delivery_metadata(
                        purchase,
                        settlement=None,
                        outcome="in_progress",
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
            self.repository.save_claimed_purchase(
                purchase,
                claim_token,
                expected_execution_mode=self._ALLOWANCE_MODE,
                expected_states={self._PAID_DELIVERY_STATE},
            )
        # A paid delivery marker carries the original input for a bounded
        # recovery window.  It is deliberately finite and is deleted after
        # successful terminalization.
        self._retain_paid_input(preview.preview_id)

        try:
            settlement = self.core.reservation(purchase.reservation_id)
            if settlement is None:
                raise ValueError("payment reservation not found")
            # Core.reconcile is intentionally the only follow-up operation.
            # It returns an existing settled/finalized row without preparing or
            # submitting a new transaction.
            settlement = self.core.reconcile(purchase.reservation_id)
        except Exception:
            return self._save_paid_delivery_pending(
                purchase,
                claim_token,
                reason="PAYMENT_RECONCILIATION_PENDING",
                settlement=None,
            )

        if not self._settled(settlement):
            return self._save_paid_delivery_pending(
                purchase,
                claim_token,
                reason="PAYMENT_RECONCILIATION_PENDING",
                settlement=settlement,
            )
        receipt = settlement.get("receipt")
        transaction_hash = settlement.get("tx_hash")
        evidence = settlement.get("budget_watcher_evidence") or {}
        if (
            not isinstance(receipt, dict)
            or not receipt
            or not transaction_hash
            or evidence.get("verified") is not True
            or evidence.get("status") != "verified"
        ):
            return self._save_paid_delivery_pending(
                purchase,
                claim_token,
                reason="PAYMENT_RECEIPT_UNAVAILABLE",
                settlement=settlement,
            )

        service_input = self.inputs.acquire(
            preview.preview_id, self._PAID_INPUT_TTL_SECONDS
        )
        if service_input is None:
            return self._save_paid_delivery_pending(
                purchase,
                claim_token,
                reason="INPUT_UNAVAILABLE_AFTER_PAYMENT",
                settlement=settlement,
            )
        if digest(service_input) != purchase.input_hash:
            return self._save_paid_delivery_pending(
                purchase,
                claim_token,
                reason="INPUT_HASH_MISMATCH",
                settlement=settlement,
            )
        try:
            self._merchant_request_payload(offering, service_input)
        except ValueError:
            return self._save_paid_delivery_pending(
                purchase,
                claim_token,
                reason="INVALID_SERVICE_INPUT",
                settlement=settlement,
            )

        receipt_token = base64.urlsafe_b64encode(
            json.dumps(receipt, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        started = time.perf_counter()
        try:
            response = self._merchant_request(
                offering,
                service_input,
                headers={
                    "X-CLINK-PAYMENT-RECEIPT": receipt_token,
                    "Idempotency-Key": purchase.purchase_id,
                },
            )
            if not response.is_success:
                raise RuntimeError("merchant delivery failed")
            service_result = self._service_result(response)
        except Exception:
            return self._save_paid_delivery_pending(
                purchase,
                claim_token,
                reason="DELIVERY_FAILED",
                settlement=settlement,
                latency_ms=self._latency_ms(started),
            )

        updated = purchase.model_copy(
            update={
                "state": "delivered",
                "output_hash": digest(service_result),
                "receipt_id": settlement.get("receipt_id")
                or settlement.get("tx_hash")
                or transaction_hash,
                "reason_code": None,
                "metadata": self._delivery_metadata(
                    purchase,
                    settlement=settlement,
                    outcome="delivered",
                ),
                "updated_at": datetime.now(UTC),
            }
        )
        self._save_result_mailbox(updated.purchase_id, service_result)
        # The shared terminal writer atomically records the successful result
        # and outbox finalization from the durable payment_submitted marker.
        # Core finalization is the only money-side operation here, and it
        # applies to the already-settled reservation.
        self._finish_terminal(
            updated,
            claim_token,
            latency_ms=self._latency_ms(started),
            input_key=preview.preview_id,
        )
        return self._response(updated, service_result)

    def _paid_delivery_pending(self, purchase, *, reason):
        """Persist a paid marker when delivery context is not currently usable."""

        claimed, claim_token = self.repository.claim_purchase_execution(
            purchase,
            allowed_states={
                self._PAID_DELIVERY_STATE,
                "payment_submitted",
            },
            expected_execution_mode=self._ALLOWANCE_MODE,
        )
        if not claim_token:
            return self._response(claimed or purchase)
        return self._save_paid_delivery_pending(
            claimed,
            claim_token,
            reason=reason,
            settlement=None,
        )

    def _save_pending_reconciliation(self, purchase, claim_token, *, settlement):
        """Close only a Core-proven revert; keep all unknown outcomes pending."""

        if self._proven_unpaid(settlement):
            updated = purchase.model_copy(
                update={
                    "state": "failed",
                    "reason_code": "PAYMENT_REVERTED",
                    "metadata": self._settlement_metadata(
                        purchase,
                        settlement=settlement,
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
            if not claim_token:
                return self._response(purchase)
            self.repository.save_claimed_purchase(
                updated,
                claim_token,
                expected_execution_mode=purchase.execution_mode,
                release_claim=True,
                expected_states={purchase.state, "payment_submitted"},
            )
            self.inputs.delete(purchase.preview_id)
            return self._response(updated)
        return super()._save_pending_reconciliation(
            purchase,
            claim_token,
            settlement=settlement,
        )

    @staticmethod
    def _proven_unpaid(settlement):
        if not isinstance(settlement, dict):
            return False
        proof = settlement.get("budget_revert_proof") or {}
        evidence = settlement.get("budget_watcher_evidence") or {}
        return (
            settlement.get("state") == "unpaid_terminal"
            and settlement.get("budget_accounting_state") == "released"
            and settlement.get("release_reason") == "verified_final_revert"
            and proof.get("validated") is True
            and proof.get("receipt_status") == 0
            and evidence.get("verified") is True
            and evidence.get("status") == "reverted"
            and evidence.get("receipt_status") == 0
            and evidence.get("two_rpc_verified") is True
        )

    def _finish_terminal(self, purchase, claim_token, *, latency_ms, input_key):
        """Persist a paid delivery failure without terminalizing Core."""

        if (
            purchase.execution_mode == self._ALLOWANCE_MODE
            and purchase.state == self._PAID_DELIVERY_STATE
        ):
            self._save_paid_delivery_pending(
                purchase,
                claim_token,
                reason=purchase.reason_code or "DELIVERY_FAILED",
                settlement=None,
                latency_ms=latency_ms,
            )
            # Deliberately retain the input for the same purchase retry.
            return
        return super()._finish_terminal(
            purchase,
            claim_token,
            latency_ms=latency_ms,
            input_key=input_key,
        )

    def _save_paid_delivery_pending(
        self,
        purchase,
        claim_token=None,
        *,
        reason,
        settlement,
        latency_ms=None,
    ):
        """Save a retryable paid marker and release only the execution claim."""

        updated = purchase.model_copy(
            update={
                "state": self._PAID_DELIVERY_STATE,
                "reason_code": reason,
                "metadata": self._delivery_metadata(
                    purchase,
                    settlement=settlement,
                    outcome="pending",
                ),
                "updated_at": datetime.now(UTC),
            }
        )
        if settlement:
            updated = updated.model_copy(
                update={
                    "receipt_id": settlement.get("receipt_id")
                    or settlement.get("tx_hash")
                    or purchase.receipt_id,
                }
            )
        if claim_token:
            self._retain_paid_input(purchase.preview_id)
            self.repository.save_claimed_purchase(
                updated,
                claim_token,
                expected_execution_mode=purchase.execution_mode,
                latency_ms=latency_ms,
                release_claim=True,
                expected_states={
                    purchase.state,
                    "payment_submitted",
                    self._PAID_DELIVERY_STATE,
                },
            )
        else:
            # This branch is only used before a paid marker can be claimed. It
            # must never create a new reservation or overwrite a newer state.
            return self._response(purchase)
        return self._response(updated)

    def _retain_paid_input(self, input_key):
        value = self.inputs.get(input_key)
        if value is not None:
            self.inputs.set(
                input_key,
                value,
                self._PAID_INPUT_TTL_SECONDS,
            )

    def _delivery_metadata(self, purchase, *, settlement, outcome):
        metadata = self._settlement_metadata(
            purchase,
            settlement=settlement,
        )
        previous = metadata.get("delivery_attempt") or {}
        attempts = int(previous.get("attempts") or 0) + (
            1 if outcome in {"pending", "delivered"} else 0
        )
        metadata["delivery_attempt"] = {
            "attempts": attempts,
            "last_outcome": outcome,
            "last_attempt_at": datetime.now(UTC).isoformat(),
        }
        if outcome == "delivered":
            metadata.pop("budget_paid_delivery", None)
        else:
            metadata["budget_paid_delivery"] = True
        return metadata
