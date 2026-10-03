"""Copied HTTP delivery handler with explicit local-chain evidence labeling."""
import hmac
from agentonomy_commerce.merchant import ReviewMerchant, reconcile_csv, _MerchantRequestError, _MerchantHandler
class BudgetMerchant(ReviewMerchant):
    def _handle_post(self, handler: _MerchantHandler) -> None:
        try:
            purchase_id, csv_text, input_hash = self._read_request(handler)
            stored = self._stored_result(purchase_id)
            if stored is not None:
                stored_hash, stored_result = stored
                if not hmac.compare_digest(stored_hash, input_hash):
                    raise _MerchantRequestError(
                        409,
                        "idempotency_conflict",
                        "Idempotency-Key was previously used with different input",
                    )
                handler._send(200, stored_result)
                return

            result = reconcile_csv(csv_text)
            payload = {
                **result,
                "purchase_id": purchase_id,
                "input_hash": input_hash,
                "real_funds": False,
                "settlement_mode": "local_anvil",
            }
            stored_result = self._store_result(purchase_id, input_hash, payload)
            handler._send(200, stored_result)
        except _MerchantRequestError as exc:
            handler._send(
                exc.status,
                {"error": exc.error, "message": exc.message},
            )
        except ValueError as exc:
            handler._send(400, {"error": "invalid_request", "message": str(exc)})
        except Exception:
            # Do not echo callback, SQLite, or receipt parser details to the
            # caller.  The process remains available for a safe retry.
            handler._send(500, {"error": "merchant_unavailable"})
