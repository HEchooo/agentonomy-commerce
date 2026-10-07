from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from examples.monad_commerce.hosted_api import create_app
from examples.monad_commerce.hosted_service import HostedCommerceService


ORIGIN = "https://commerce.example"
OWNER = "0x" + "aa" * 20
AGENT_TOKEN = "agent-token"


def configuration() -> dict[str, object]:
    network = {
        "mode": "monad_testnet",
        "chain_id": 10143,
        "rpc_urls": ["https://rpc.example.org", "https://rpc.example.net"],
        "token": "0x" + "11" * 20,
        "executor": "0x" + "22" * 20,
        "payee": "0x" + "33" * 20,
    }
    signer = {
        "profile": "hackathon",
        "region": "ap-southeast-1",
        "account_id": "123456789012",
        "expected_role_arn": "arn:aws:iam::123456789012:role/test-runtime",
        "credential_directory": "/tmp/isolated-hosted-security-test",
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
            "owner": OWNER,
            "execution_address": "0x" + "44" * 20,
            "relayer_address": "0x" + "55" * 20,
            "nonce_min": 3,
            "nonce_max": 5,
        },
    }
    return {
        "deployment": network
        | {
            "owner": OWNER,
            "execution_signer": "0x" + "44" * 20,
            "relayer": "0x" + "55" * 20,
        },
        "signer_configuration": signer,
    }


class SecurityCanary:
    created: list["SecurityCanary"] = []

    def __init__(self, path: Path, config: dict[str, object], **kwargs: object):
        self.path = path
        self.config = config
        self.owner = config["deployment"]["owner"]  # type: ignore[index]
        self.tenant_id = kwargs["tenant_id"]
        self.authorized: set[str] = set()
        self.revoked = False
        self.expired = False
        self.request_calls: list[tuple[str, dict[str, object]]] = []
        self.scope_extra: dict[str, object] = {}
        type(self).created.append(self)

    def __enter__(self) -> "SecurityCanary":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def hosted(self, method: str, **params: object) -> dict[str, object] | None:
        digest = params.get("browser_digest")
        if method == "browser_resume":
            return None
        if method == "browser_challenge":
            return {"session_id": "challenge-security", "message_to_sign": self.owner}
        if method == "browser_verify":
            if params.get("signature") != self.owner:
                raise ValueError("wallet proof rejected")
            self.authorized.add(digest)  # type: ignore[arg-type]
            return {"wallet_address": self.owner}
        if method == "browser_authorize":
            if self.revoked or digest not in self.authorized:
                raise ValueError("wallet authorization revoked")
            return {"wallet_address": self.owner}
        if method == "opc_token":
            if self.revoked:
                raise ValueError("OPC token revoked")
            return {"access_token": AGENT_TOKEN}
        if method == "opc_authenticate":
            if self.revoked or self.expired or params.get("access_token") != AGENT_TOKEN:
                raise ValueError("OPC token revoked")
            principal: dict[str, object] = {
                "issuer": "opc",
                "user_id": "commerce-demo-user",
                "agent_id": "hermes",
                "scope": "payments",
                "wallet_identity_id": "identity-security",
                "spending_grant_id": "grant-security",
                "installation_id": "installation-security",
                "credential_id": "credential-security",
                "expires_at": 4102444800,
            }
            principal.update(deepcopy(self.scope_extra))
            return principal
        raise AssertionError(method)

    def request(self, method: str, arguments: dict[str, object]) -> dict[str, object]:
        self.request_calls.append((method, dict(arguments)))
        return {"services": []}


@pytest.fixture
def service(tmp_path: Path):
    SecurityCanary.created = []
    with HostedCommerceService(
        tmp_path,
        configuration(),
        public_origin=ORIGIN,
        canary_factory=SecurityCanary,
    ) as hosted:
        yield hosted


def login(hosted: HostedCommerceService) -> tuple[str, str, SecurityCanary]:
    browser_token, csrf_token = "x" * 43, "y" * 43
    hosted.session(browser_token, csrf_token)
    challenge = hosted.login_challenge(browser_token, csrf_token, OWNER)
    hosted.login_verify(browser_token, csrf_token, challenge["session_id"], OWNER)
    return browser_token, csrf_token, SecurityCanary.created[-1]


def test_revoke_after_mcp_admission_blocks_callback_without_business_call(service, monkeypatch):
    browser_token, _csrf_token, canary = login(service)
    original_authenticate = service.authenticate
    admission_calls = 0

    def authenticate(access_token):
        nonlocal admission_calls
        principal = original_authenticate(access_token)
        admission_calls += 1
        if admission_calls == 1:
            canary.revoked = True
        return principal

    monkeypatch.setattr(service, "authenticate", authenticate)

    with pytest.raises(RuntimeError, match="hosted MCP"):
        service.dispatch(browser_token, None, "search", {"query": "csv"})

    assert admission_calls >= 1
    assert canary.request_calls == []
    assert service._agent_credentials == {}


def test_expiry_after_mcp_admission_blocks_callback_without_business_call(service, monkeypatch):
    browser_token, _csrf_token, canary = login(service)
    original_authenticate = service.authenticate
    admission_calls = 0

    def authenticate(access_token):
        nonlocal admission_calls
        principal = original_authenticate(access_token)
        admission_calls += 1
        if admission_calls == 1:
            canary.expired = True
        return principal

    monkeypatch.setattr(service, "authenticate", authenticate)

    with pytest.raises(RuntimeError, match="hosted MCP"):
        service.dispatch(browser_token, None, "search", {"query": "csv"})

    assert admission_calls >= 1
    assert canary.request_calls == []
    assert service._agent_credentials == {}


def test_agent_credential_mapping_is_cleaned_after_exception(service, monkeypatch):
    browser_token, _csrf_token, canary = login(service)

    async def fail_call(**_kwargs):
        raise RuntimeError("callback failed")

    from examples.monad_commerce import hosted_mcp

    monkeypatch.setattr(hosted_mcp, "call_hosted_tool", fail_call)
    with pytest.raises(RuntimeError, match="callback failed"):
        service.dispatch(browser_token, None, "search", {"query": "csv"})

    assert canary.request_calls == []
    assert service._agent_credentials == {}


def test_agent_credential_mapping_is_cleaned_after_cancellation(service, monkeypatch):
    browser_token, _csrf_token, canary = login(service)

    async def cancel_call(**_kwargs):
        raise asyncio.CancelledError

    from examples.monad_commerce import hosted_mcp

    monkeypatch.setattr(hosted_mcp, "call_hosted_tool", cancel_call)
    with pytest.raises(asyncio.CancelledError):
        service.dispatch(browser_token, None, "search", {"query": "csv"})

    assert canary.request_calls == []
    assert service._agent_credentials == {}


@pytest.mark.parametrize("extra", ["owner", "tenant", "agent", "access_token"])
def test_mcp_scope_rejects_unexpected_identity_fields(extra):
    principal = {
        "issuer": "opc",
        "user_id": "commerce-demo-user",
        "agent_id": "hermes",
        "scope": "payments",
        "wallet_identity_id": "identity-security",
        "spending_grant_id": "grant-security",
        "installation_id": "installation-security",
        "credential_id": "credential-security",
        "expires_at": 4102444800,
        extra: "unexpected",
    }

    with pytest.raises(PermissionError):
        HostedCommerceService._mcp_scope(principal)


class SecretResponseService:
    def __enter__(self) -> "SecretResponseService":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def session(self, _browser_token: str, _csrf_token: str | None = None):
        return {"authenticated": True, "owner": OWNER}

    def dispatch(self, _browser_token: str, _csrf_token: str | None, _operation: str, _args):
        return {"access_token": "public-secret-marker"}


def test_public_response_rejects_secret_without_echoing_token():
    app = create_app(origin=ORIGIN, runtime_factory=SecretResponseService)
    with TestClient(app, base_url=ORIGIN) as client:
        session = client.post("/api/session", headers={"Origin": ORIGIN}, json={})
        assert session.status_code == 200, session.text
        response = client.get("/api/status")

    assert response.status_code == 409
    assert "public-secret-marker" not in response.text
    assert "access_token" not in response.text
