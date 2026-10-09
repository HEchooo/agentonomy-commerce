from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from examples.monad_commerce.hosted_api import create_app
from examples.monad_commerce.hosted_service import HostedCommerceService


ORIGIN = "https://commerce.example"
OWNER_A = "0x" + "11" * 20
OWNER_B = "0x" + "22" * 20
SIGNATURE = "0x" + "aa" * 65
TX_HASH = "0x" + "bb" * 32


def configuration():
    network = {
        "mode": "monad_testnet",
        "chain_id": 10143,
        "rpc_urls": ["https://rpc.example.org", "https://rpc.example.net"],
        "token": "0x" + "33" * 20,
        "executor": "0x" + "44" * 20,
        "payee": "0x" + "55" * 20,
    }
    signer = {
        "profile": "hackathon",
        "region": "ap-southeast-1",
        "account_id": "123456789012",
        "expected_role_arn": "arn:aws:iam::123456789012:role/test-runtime",
        "credential_directory": "/tmp/isolated-hosted-http-test",
        "execution_key_arn": (
            "arn:aws:kms:ap-southeast-1:123456789012:key/"
            "11111111-1111-4111-8111-111111111111"
        ),
        "gas_key_arn": (
            "arn:aws:kms:ap-southeast-1:123456789012:key/"
            "22222222-2222-4222-8222-222222222222"
        ),
        "scope": {
            "network": network,
            "owner": OWNER_A,
            "execution_address": "0x" + "66" * 20,
            "relayer_address": "0x" + "77" * 20,
            "nonce_min": 3,
            "nonce_max": 5,
        },
    }
    return {
        "deployment": network
        | {
            "owner": OWNER_A,
            "execution_signer": "0x" + "66" * 20,
            "relayer": "0x" + "77" * 20,
        },
        "signer_configuration": signer,
    }


class TenantCanary:
    """Small Core/Marketplace boundary fake with durable tenant order state."""

    instances: list["TenantCanary"] = []
    orders_by_tenant: dict[str, dict[str, dict[str, object]]] = {}
    authorized_by_tenant: dict[str, set[str]] = {}

    def __init__(self, path: Path, config: dict[str, object], **kwargs):
        self.path = path
        self.config = config
        self.owner = config["deployment"]["owner"]
        self.tenant_id = kwargs["tenant_id"]
        self.orders = self.orders_by_tenant.setdefault(self.tenant_id, {})
        self.authorized = self.authorized_by_tenant.setdefault(self.tenant_id, set())
        self.events: list[tuple[str, dict[str, object]]] = []
        self.opc_active = False
        self.opc_access_token = f"opaque-{self.tenant_id}"
        self.grant_ready = False
        self.budget_ready = False
        self.allowance_ready = False
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def _event(self, name: str, **params):
        self.events.append((name, params))

    def hosted(self, method: str, **params):
        self._event(method, **params)
        digest = params.get("browser_digest")
        if method == "browser_resume":
            return None
        if method == "browser_challenge":
            return {
                "session_id": f"login-{self.tenant_id}",
                "message_to_sign": f"login:{self.owner}",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        if method == "browser_verify":
            if params["signature"] != SIGNATURE:
                raise ValueError("wallet proof rejected")
            self.authorized.add(digest)
            return {"wallet_address": self.owner}
        if method == "browser_authorize":
            if digest not in self.authorized:
                raise ValueError("wallet session unavailable")
            return {"wallet_address": self.owner}
        if method == "browser_logout":
            self.authorized.discard(digest)
            return {"status": "logged_out"}
        if method == "opc_pair":
            if not params.get("proof"):
                raise ValueError("server OPC proof required")
            return {
                "pairing_id": f"pair-{self.tenant_id}",
                "installation_id": f"installation-{self.tenant_id}",
                "session_id": f"opc-{self.tenant_id}",
                "message_to_sign": f"approve:{self.owner}",
            }
        if method == "opc_approve":
            if params["signature"] != SIGNATURE:
                raise ValueError("OPC approval rejected")
            self.opc_active = True
            return {"installation_id": f"installation-{self.tenant_id}"}
        if method == "opc_token":
            if not self.opc_active or not params.get("proof"):
                raise ValueError("OPC token unavailable")
            return {"access_token": self.opc_access_token}
        if method == "opc_authenticate":
            if params.get("access_token") != self.opc_access_token:
                raise ValueError("OPC authentication rejected")
            return {
                "issuer": "opc", "scope": "payments", "agent_id": "hermes",
                "user_id": "commerce-demo-user", "expires_at": 4102444800,
                "wallet_identity_id": f"identity-{self.tenant_id}",
                "spending_grant_id": f"grant-{self.tenant_id}",
                "installation_id": f"installation-{self.tenant_id}",
                "credential_id": f"credential-{self.tenant_id}",
            }
        if method == "opc_status":
            return {
                "status": "active" if self.opc_active else "unpaired",
                "installation_id": f"installation-{self.tenant_id}",
            }
        raise AssertionError(method)

    def onboarding(self, method: str, **params):
        self._event(method, **params)
        if method == "grant_challenge":
            return {"session_id": f"grant-{self.tenant_id}", "message_to_sign": "grant"}
        if method == "grant_verify":
            if params["signature"] != SIGNATURE:
                raise ValueError("grant proof rejected")
            self.grant_ready = True
            return {"status": "grant_verified"}
        if method == "budget_payload":
            return {"budget": {"amount": "0.30", "asset": "TestUSD"}}
        if method == "budget_bind":
            if params["signature"] != SIGNATURE or not self.grant_ready:
                raise ValueError("budget proof rejected")
            self.budget_ready = True
            return {"status": "budget_bound"}
        raise AssertionError(method)

    def approval_transaction(self, **params):
        self._event("approval_transaction", **params)
        return {"to": self.config["deployment"]["token"], "data": "0xapprove"}

    def verify_approval(self, tx_hash):
        self._event("verify_approval", tx_hash=tx_hash)
        if tx_hash != TX_HASH or not self.budget_ready:
            raise ValueError("allowance proof rejected")
        self.allowance_ready = True
        return {"status": "allowance_verified"}

    def status(self):
        return {
            "owner": self.owner,
            "onboarding": {"grant": self.grant_ready, "budget": self.budget_ready},
        }

    def request(self, method: str, args: dict[str, object]):
        self._event("request", method=method, arguments=deepcopy(args))
        if method == "search":
            return {
                "services": [
                    {
                        "offering_id": "csv-reconciliation-v1",
                        "title": "CSV reconciliation",
                    }
                ]
            }
        if method == "details":
            if args["offering_id"] != "csv-reconciliation-v1":
                raise KeyError("offering_not_found")
            return {"offering_id": args["offering_id"], "price": "0.30"}
        if method == "preview":
            preview_id = f"preview-{self.tenant_id}"
            self.preview = {
                "preview_id": preview_id,
                "offering_id": args["offering_id"],
                "state": "created",
                "expires_at": "2099-01-01T00:00:00Z",
                "created_at": "2099-01-01T00:00:00Z",
                "payment": {"asset": "TestUSD", "price_usd": "0.30"},
                "quote_hash": "quote-tenant",
                "input_hash": "input-tenant",
            }
            return dict(self.preview)
        if method == "execute":
            if args["preview_id"] != self.preview["preview_id"]:
                raise KeyError("preview_not_found")
            purchase_id = f"purchase-{self.tenant_id}"
            order = {
                "purchase_id": purchase_id,
                "preview_id": args["preview_id"],
                "offering_id": "csv-reconciliation-v1",
                "state": "delivered",
                "execution_mode": "monad_testnet",
                "reason_code": "DELIVERED",
                "receipt_id": f"receipt-{self.tenant_id}",
                "input_hash": "input-tenant",
                "output_hash": "output-tenant",
                "created_at": "2099-01-01T00:00:00Z",
                "updated_at": "2099-01-01T00:00:00Z",
                "service_result": {"rows": 2, "owner": self.owner},
            }
            self.orders[purchase_id] = order
            return dict(order)
        if method == "purchase":
            purchase_id = args["purchase_id"]
            if purchase_id not in self.orders:
                raise KeyError("purchase_not_found")
            return dict(self.orders[purchase_id])
        if method == "recover_purchase":
            purchase_id = args["purchase_id"]
            if purchase_id not in self.orders:
                raise KeyError("purchase_not_found")
            return dict(self.orders[purchase_id])
        raise AssertionError(method)


def app_for(tmp_path):
    TenantCanary.instances = []
    TenantCanary.orders_by_tenant = {}
    TenantCanary.authorized_by_tenant = {}

    def runtime_factory():
        return HostedCommerceService(
            tmp_path,
            configuration(),
            public_origin=ORIGIN,
            canary_factory=TenantCanary,
        )

    return create_app(origin=ORIGIN, runtime_factory=runtime_factory)


def mutation_headers(csrf: str) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-Agentonomy-CSRF": csrf}


def new_session(client: TestClient) -> str:
    response = client.post("/api/session", headers={"Origin": ORIGIN}, json={})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def login(client: TestClient, owner: str) -> str:
    csrf = new_session(client)
    headers = mutation_headers(csrf)
    challenge_response = client.post(
        "/api/login/challenge", headers=headers, json={"owner": owner}
    )
    assert challenge_response.status_code == 200, challenge_response.text
    challenge = challenge_response.json()
    verify_response = client.post(
        "/api/login/verify",
        headers=headers,
        json={"challenge_id": challenge["session_id"], "signature": SIGNATURE},
    )
    assert verify_response.status_code == 200, verify_response.text
    return csrf


def assert_no_access_token(response):
    assert response.status_code == 200, response.text
    assert "access_token" not in response.text
    assert "opc_token" not in response.text


def test_http_cookie_login_onboarding_opc_and_mcp_market_flow_is_owner_scoped(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = login(client, OWNER_A)
        headers = mutation_headers(csrf)

        grant = client.post("/api/grant/challenge", headers=headers, json={})
        assert grant.status_code == 200, grant.text
        grant_verify = client.post(
            "/api/grant/verify", headers=headers, json={"signature": SIGNATURE}
        )
        assert grant_verify.status_code == 200, grant_verify.text
        budget_payload = client.post("/api/budget/payload", headers=headers, json={})
        assert budget_payload.status_code == 200, budget_payload.text
        budget_bind = client.post(
            "/api/budget/bind", headers=headers, json={"signature": SIGNATURE}
        )
        assert budget_bind.status_code == 200, budget_bind.text
        allowance = client.get("/api/allowance/transaction", headers={"Origin": ORIGIN})
        assert allowance.status_code == 200, allowance.text
        allowance_verify = client.post(
            "/api/allowance/verify",
            headers=headers,
            json={"transaction_hash": TX_HASH},
        )
        assert allowance_verify.status_code == 200, allowance_verify.text

        pairing = client.post("/api/opc/prepare", headers=headers, json={})
        assert pairing.status_code == 200, pairing.text
        approve = client.post(
            "/api/opc/approve",
            headers=headers,
            json={
                "challenge_id": pairing.json()["session_id"],
                "signature": SIGNATURE,
            },
        )
        assert approve.status_code == 200, approve.text
        assert "access_token" not in pairing.text
        assert "access_token" not in approve.text

        search = client.get("/api/services?query=csv", headers={"Origin": ORIGIN})
        assert_no_access_token(search)
        preview = client.post(
            "/api/preview",
            headers=headers,
            json={
                "csv_text": "id,amount\n1,10.00\n",
                "idempotency_key": "http-flow-a",
            },
        )
        assert_no_access_token(preview)
        execute = client.post(
            "/api/execute",
            headers=headers,
            json={"preview_id": preview.json()["preview_id"]},
        )
        assert_no_access_token(execute)
        purchase_id = execute.json()["purchase_id"]

        canary = next(item for item in TenantCanary.instances if item.owner == OWNER_A)
        request_indexes = [
            index
            for index, event in enumerate(canary.events)
            if event[0] == "request" and event[1]["method"] in {"search", "preview", "execute"}
        ]
        assert len(request_indexes) == 3
        previous = 0
        for index in request_indexes:
            admission = [event[0] for event in canary.events[previous:index]]
            assert "opc_token" in admission
            # The actual MCP gateway rechecks Core at HTTP admission and at
            # its tool callback; a direct proxy call cannot satisfy this.
            assert admission.count("opc_authenticate") >= 3
            previous = index + 1
        service = app.state.hosted_service
        assert not service._agent_credentials
        with pytest.raises(PermissionError):
            service.authenticate(canary.opc_access_token)

        client.cookies.clear()
        csrf_b = login(client, OWNER_B)
        other = client.get(
            f"/api/purchases/{purchase_id}", headers={"Origin": ORIGIN}
        )
        assert other.status_code in {404, 409}
        assert OWNER_A not in other.text
        recover = client.post(
            f"/api/purchases/{purchase_id}/recover",
            headers=mutation_headers(csrf_b),
            json={},
        )
        assert recover.status_code in {404, 409}
        assert OWNER_A not in recover.text


def test_http_csrf_expiry_and_revoke_race_are_fresh_authorization_checks(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = login(client, OWNER_A)
        canary = next(item for item in TenantCanary.instances if item.owner == OWNER_A)
        canary.authorized.clear()

        read = client.get("/api/services", headers={"Origin": ORIGIN})
        assert read.status_code == 401
        mutation = client.post(
            "/api/revoke/prepare",
            headers=mutation_headers(csrf),
            json={},
        )
        assert mutation.status_code == 401

        client.cookies.clear()
        csrf = new_session(client)
        # A fresh browser session cannot inherit the old wallet's authorization.
        status = client.get("/api/status", headers={"Origin": ORIGIN})
        assert status.status_code == 200
        assert status.json()["authenticated"] is False
        assert csrf
