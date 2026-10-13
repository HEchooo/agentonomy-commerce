"""Marketplace-only process adapter; Core remains behind its private pipe."""
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from agentonomy_commerce.runtime import ReviewRuntime
from examples.monad_commerce.public_runtime import PublicCommerceRuntime
from examples.monad_commerce.hosted_process import HostedCoreBridge
from agentonomy_commerce.runtime import OFFERING_ID, RESOURCE, USER_ID, reconcile_csv, digest
from storage.tables import OfferingRow


_FIRST_PARTY_PROVIDER_ID = "commerce_analytics"
_MERCHANT_RECONCILE_PATH = "/v1/reconcile"
_MERCHANT_HEALTH_PATH = "/health"


class HostedCommerceRuntime(PublicCommerceRuntime):
    def __init__(self, state_dir, merchant_port, bootstrap):
        ReviewRuntime.__init__(self, state_dir, merchant_port)
        self.core = HostedCoreBridge(self.state_dir, bootstrap)

    def hosted(self, method, params):
        return self.core.hosted(method, **params)

    def onboarding(self, operation, **params):
        return self.core.onboarding(operation, **params)

    @staticmethod
    def _health_endpoint(endpoint):
        if not isinstance(endpoint, str):
            raise ValueError("merchant endpoint is invalid")
        parsed = urlsplit(endpoint)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("merchant endpoint is invalid") from exc
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path != _MERCHANT_RECONCILE_PATH
            or not isinstance(port, int)
            or not 1 <= port <= 65535
            or parsed.netloc != f"127.0.0.1:{port}"
        ):
            raise ValueError("merchant endpoint is invalid")
        return f"http://127.0.0.1:{port}{_MERCHANT_HEALTH_PATH}"

    def _refresh_first_party_catalog(self):
        try:
            if self.provider is None or self.provider.provider_id != _FIRST_PARTY_PROVIDER_ID:
                raise ValueError("first-party provider is invalid")
            if len(self.offerings) != 1:
                raise ValueError("first-party catalog is ambiguous")
            offering = self.offerings[0]
            if (
                offering.offering_id != OFFERING_ID
                or offering.provider_id != _FIRST_PARTY_PROVIDER_ID
                or offering.endpoint != RESOURCE
                or len(offering.payment_options) != 1
            ):
                raise ValueError("first-party offering is invalid")
            payment = offering.payment_options[0]
            if (
                payment.network != self._network
                or payment.asset != self._asset
                or payment.pay_to != self._pay_to
            ):
                raise ValueError("first-party payment scope is invalid")

            health_endpoint = self._health_endpoint(self.merchant.endpoint)
            response = httpx.get(
                health_endpoint,
                timeout=2.0,
                trust_env=False,
                follow_redirects=False,
            )
            if response.status_code != 200:
                raise ValueError("merchant health is unavailable")
            health = response.json()
            expected = {
                "status": "ready",
                "merchant_id": _FIRST_PARTY_PROVIDER_ID,
                "network": self._network,
                "token": self._asset,
                "pay_to": self._pay_to,
                "resource": RESOURCE,
            }
            if type(health) is not dict or health != expected:
                raise ValueError("merchant health scope is invalid")

            checked_at = datetime.now(UTC)
            expected_payload = offering.model_dump(mode="json")
            with self.repository.sessions.begin() as session:
                row = session.get(OfferingRow, OFFERING_ID)
                if (
                    row is None
                    or row.provider_id != _FIRST_PARTY_PROVIDER_ID
                    or row.status != "verified"
                    or row.candidate_payload is not None
                    or row.payload != expected_payload
                ):
                    raise ValueError("first-party catalog row is not the startup snapshot")
                row.last_verified_at = checked_at
        except Exception:
            raise RuntimeError("merchant unavailable") from None

    def search(self, query=""):
        self._refresh_first_party_catalog()
        return super().search(query)

    def details(self, offering_id):
        self._refresh_first_party_catalog()
        return super().details(offering_id)

    def preview_state(self, preview_id):
        preview = self.repository.get_preview(preview_id)
        if preview is None:
            raise KeyError('preview not found')
        return preview.model_dump(mode='json')

    def _device_preview(self, preview_id, opc_installation_id):
        preview = self.repository.get_preview(preview_id)
        if preview is None:
            raise KeyError('preview not found')
        if preview.opc_installation_id != opc_installation_id:
            raise ValueError('OPC installation mismatch')
        return preview

    def preview(self, offering_id, csv_text, idempotency_key, opc_installation_id=None):
        if opc_installation_id is None:
            return super().preview(offering_id, csv_text, idempotency_key)
        # The same copied preview/idempotency adapter, now with the trusted
        # device scope passed to canonical Marketplace and Core authorization.
        self._require_started()
        if offering_id != OFFERING_ID:
            raise KeyError('offering not found')
        reconcile_csv(csv_text)
        service_input = {'csv_text': csv_text}
        request_hash = digest({'offering_id': offering_id, 'service_input': service_input})
        cache_key = 'device_preview_request:' + digest({'installation': opc_installation_id,
                                                      'key': idempotency_key})
        previous = self.store.get(cache_key)
        if previous is not None:
            if previous['request_hash'] != request_hash:
                raise ValueError('idempotency_conflict')
            return self._device_preview(previous['preview_id'], opc_installation_id).model_dump(mode='json')
        preview = self.purchases.create_preview(user_id=USER_ID, offering_id=offering_id,
                                                service_input=service_input,
                                                opc_installation_id=opc_installation_id)
        self.store.set(cache_key, {'request_hash': request_hash, 'preview_id': preview.preview_id}, 30 * 86400)
        return preview.model_dump(mode='json')

    def execute(self, preview_id, opc_installation_id=None):
        if opc_installation_id is None:
            return super().execute(preview_id)
        self._require_started()
        self._device_preview(preview_id, opc_installation_id)
        return self._execution_payload(self.purchases.execute(preview_id, opc_installation_id=opc_installation_id))

    def purchase(self, purchase_id, opc_installation_id=None):
        if opc_installation_id is not None:
            purchase = self.repository.get_purchase(purchase_id)
            if purchase is None:
                raise KeyError('purchase not found')
            self._device_preview(purchase.preview_id, opc_installation_id)
        return super().purchase(purchase_id)

    def recover_purchase(self, purchase_id, csv_text=None, opc_installation_id=None):
        if opc_installation_id is None:
            return super().recover_purchase(purchase_id, csv_text)
        purchase = self.repository.get_purchase(purchase_id)
        if purchase is None:
            raise KeyError('purchase not found')
        preview = self._device_preview(purchase.preview_id, opc_installation_id)
        if csv_text is not None:
            # Preserve the existing paid-input recovery checks and canonical
            # restore_paid_input implementation; no reserve or payment retry.
            if not isinstance(csv_text, str) or not 1 <= len(csv_text) <= 131072:
                raise ValueError('invalid recovery input')
            if (purchase.user_id != preview.user_id or purchase.offering_id != preview.offering_id
                or purchase.input_hash != preview.input_hash
                or purchase.execution_mode != 'clink_allowance' or preview.execution_mode != 'clink_allowance'
                or purchase.state not in {'paid_but_undelivered', 'payment_submitted'}
                or not purchase.reservation_id):
                raise ValueError('purchase is not eligible for paid recovery')
            reservation = self.core.reservation(purchase.reservation_id)
            if not self._core_scope_matches(purchase, preview, reservation):
                raise ValueError('Core reservation scope mismatch')
            settlement = self.core.reconcile(purchase.reservation_id)
            if not self._core_scope_matches(purchase, preview, settlement):
                raise ValueError('Core settlement scope mismatch')
            self.purchases.restore_paid_input(purchase, preview, {'csv_text': csv_text}, settlement)
        return self.execute(purchase.preview_id, opc_installation_id=opc_installation_id)
