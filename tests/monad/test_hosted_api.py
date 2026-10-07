from __future__ import annotations

import asyncio
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient


ORIGIN = "https://commerce.example"
LOOPBACK_ORIGIN = "http://127.0.0.1:8091"
OWNER = "0x" + "11" * 20
SIGNATURE = "0x" + "aa" * 65
TX_HASH = "0x" + "bb" * 32


class FakeHostedService:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[str, str, str | None, dict[str, object]]] = []
        self.login_challenges: list[tuple[str, str, str]] = []
        self.expired = False
        self.raise_secret: str | None = None
        self.raise_permission = False
        self.return_opc_token = False
        self.return_response: dict[str, object] | None = None
        self.session_response: dict[str, object] | None = None

    def __enter__(self) -> "FakeHostedService":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def session(self, browser_token: str, csrf_token: str | None) -> dict[str, object]:
        if self.expired and browser_token in self.sessions:
            raise PermissionError("backend session expired; secret marker must not escape")
        if browser_token not in self.sessions:
            if csrf_token is None:
                raise ValueError("browser session is unavailable")
            self.sessions[browser_token] = {
                "csrf_token": csrf_token,
                "authenticated": False,
                "tenant": f"tenant-{len(self.sessions) + 1}",
            }
        record = self.sessions[browser_token]
        if csrf_token is not None and csrf_token != record["csrf_token"]:
            raise PermissionError("CSRF mismatch")
        result = {
            "authenticated": record["authenticated"],
            "tenant": record["tenant"],
        }
        if self.session_response is not None:
            result.update(self.session_response)
        return result

    def login_challenge(
        self, browser_token: str, csrf_token: str, owner: str
    ) -> dict[str, object]:
        self._check(browser_token, csrf_token)
        if self.raise_permission:
            raise PermissionError("backend session revoked")
        challenge = f"challenge-{len(self.login_challenges) + 1}"
        self.login_challenges.append((challenge, owner, browser_token))
        return {"challenge_id": challenge, "message_to_sign": "sign this"}

    def login_verify(
        self,
        browser_token: str,
        csrf_token: str,
        challenge_id: str,
        signature: str,
    ) -> dict[str, object]:
        self._check(browser_token, csrf_token)
        if self.raise_permission:
            raise PermissionError("backend session revoked")
        if not signature:
            raise ValueError("signature rejected")
        for challenge, _owner, challenge_browser in self.login_challenges:
            if challenge == challenge_id and challenge_browser == browser_token:
                self.sessions[browser_token]["authenticated"] = True
                return {"authenticated": True, "wallet_address": OWNER}
        raise ValueError("challenge rejected")

    def logout(self, browser_token: str, csrf_token: str) -> dict[str, object]:
        self._check(browser_token, csrf_token)
        self.sessions.pop(browser_token, None)
        return {"status": "logged_out"}

    def dispatch(
        self,
        browser_token: str,
        csrf_token: str | None,
        operation: str,
        args: dict[str, object],
    ) -> dict[str, object]:
        self._check(browser_token, csrf_token)
        if self.raise_permission:
            raise PermissionError("backend session revoked")
        if self.raise_secret is not None:
            raise RuntimeError(self.raise_secret)
        self.calls.append((browser_token, operation, csrf_token, args))
        if self.return_response is not None:
            return self.return_response
        if operation == "opc_prepare" and self.return_opc_token:
            return {"token": "private-opc-token"}
        if operation == "status":
            return {
                "authenticated": self.sessions[browser_token]["authenticated"],
                "tenant": self.sessions[browser_token]["tenant"],
                "publicToken": "public-catalog-token",
                "address": OWNER,
            }
        if operation == "opc_prepare":
            return {"installation_id": "installation-1", "message_to_sign": "install"}
        return {"operation": operation, "args": args}

    def _check(self, browser_token: str, csrf_token: str | None) -> None:
        record = self.sessions.get(browser_token)
        if record is None:
            raise PermissionError("browser session is unavailable")
        if csrf_token is not None and csrf_token != record["csrf_token"]:
            raise PermissionError("CSRF mismatch")


def app_for(
    service: FakeHostedService | None = None,
    *,
    origin: str = ORIGIN,
    allow_loopback_http: bool = False,
):
    from examples.monad_commerce import hosted_api

    backend = service or FakeHostedService()
    app = hosted_api.create_app(
        origin=origin,
        runtime_factory=lambda: backend,
        allow_loopback_http=allow_loopback_http,
    )
    return app, backend


def session(client: TestClient) -> str:
    origin = str(client.base_url).rstrip("/")
    response = client.post("/api/session", headers={"Origin": origin}, json={})
    assert response.status_code == 200
    csrf = response.json()["csrf_token"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", csrf)
    return csrf


def mutation_headers(csrf: str, origin: str = ORIGIN) -> dict[str, str]:
    return {"Origin": origin, "X-Agentonomy-CSRF": csrf}


def test_https_session_sets_secure_httponly_cookie_and_returns_only_csrf(
    tmp_path: Path,
):
    app, service = app_for()
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = session(client)
        cookie = client.cookies.get("agentonomy_hosted_session")
        assert cookie is not None and len(cookie) == 43
        header = client.post(
            "/api/session", headers={"Origin": ORIGIN}, json={}
        ).headers["set-cookie"]
        assert "agentonomy_hosted_session=" in header
        assert "HttpOnly" in header
        assert "Secure" in header
        assert "SameSite=strict" in header
        reused = client.post(
            "/api/session", headers={"Origin": ORIGIN}, json={}
        )
        assert "csrf_token" in reused.json()
        assert "agentonomy_hosted_session" not in reused.json()
        assert "access_token" not in header
        assert len(service.sessions) == 1


def test_session_regenerates_both_cookies_when_existing_session_expires():
    service = FakeHostedService()
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        session(client)
        old_browser = client.cookies.get("agentonomy_hosted_session")
        old_csrf = client.cookies.get("agentonomy_hosted_csrf")
        service.expired = True

        response = client.post("/api/session", headers={"Origin": ORIGIN}, json={})

        assert response.status_code == 200
        new_browser = client.cookies.get("agentonomy_hosted_session")
        new_csrf = client.cookies.get("agentonomy_hosted_csrf")
        assert new_browser != old_browser
        assert new_csrf != old_csrf
        assert service.sessions[old_browser]["csrf_token"] == old_csrf
        assert service.sessions[new_browser]["csrf_token"] == new_csrf


def test_session_does_not_bind_forged_csrf_to_existing_browser_cookie():
    service = FakeHostedService()
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        session(client)
        old_browser = client.cookies.get("agentonomy_hosted_session")
        old_csrf = client.cookies.get("agentonomy_hosted_csrf")
        client.cookies.jar.clear(
            domain="commerce.example", path="/", name="agentonomy_hosted_csrf"
        )
        client.cookies.set(
            "agentonomy_hosted_csrf",
            "A" * 43,
            domain="commerce.example",
            path="/",
        )

        response = client.post("/api/session", headers={"Origin": ORIGIN}, json={})

        assert response.status_code == 200
        new_browser = client.cookies.get("agentonomy_hosted_session")
        assert new_browser != old_browser
        assert service.sessions[old_browser]["csrf_token"] == old_csrf


def test_session_does_not_return_backend_token_in_bootstrap_payload():
    service = FakeHostedService()
    service.session_response = {"token": "browser-secret"}
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post("/api/session", headers={"Origin": ORIGIN}, json={})

    assert response.status_code == 409
    assert "browser-secret" not in response.text


def test_loopback_http_requires_explicit_opt_in_and_cookie_is_not_secure():
    with pytest.raises(ValueError):
        app_for(origin=LOOPBACK_ORIGIN)
    app, _service = app_for(origin=LOOPBACK_ORIGIN, allow_loopback_http=True)
    with TestClient(app, base_url=LOOPBACK_ORIGIN) as client:
        session(client)
        header = client.post(
            "/api/session", headers={"Origin": LOOPBACK_ORIGIN}, json={}
        ).headers["set-cookie"]
        assert "HttpOnly" in header
        assert "Secure" not in header
        assert "SameSite=strict" in header


def test_unauthenticated_and_expired_requests_are_redacted():
    service = FakeHostedService()
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        missing = client.get("/api/status")
        assert missing.status_code == 401
        csrf = session(client)
        service.expired = True
        expired = client.get("/api/status")
        assert expired.status_code == 401
        assert expired.json() == {"error": "authentication_required"}
        assert "secret marker" not in expired.text
        assert csrf


def test_mutations_require_exact_origin_and_csrf_before_backend_dispatch():
    service = FakeHostedService()
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = session(client)
        wrong_origin = client.post(
            "/api/grant/challenge",
            headers=mutation_headers(csrf, "https://attacker.example"),
            json={},
        )
        missing_csrf = client.post(
            "/api/grant/challenge", headers={"Origin": ORIGIN}, json={}
        )
        wrong_csrf = client.post(
            "/api/grant/challenge",
            headers=mutation_headers("A" * len(csrf)),
            json={},
        )
        assert [r.status_code for r in (wrong_origin, missing_csrf, wrong_csrf)] == [
            403,
            403,
            403,
        ]
        assert service.calls == []


def test_login_challenge_does_not_accept_identity_overrides_and_verify_binds_session():
    service = FakeHostedService()
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = session(client)
        extra = client.post(
            "/api/login/challenge",
            headers=mutation_headers(csrf),
            json={"owner": OWNER, "user_id": "attacker"},
        )
        assert extra.status_code == 422
        assert "attacker" not in extra.text

        challenge = client.post(
            "/api/login/challenge",
            headers=mutation_headers(csrf),
            json={"owner": OWNER},
        )
        assert challenge.status_code == 200
        verified = client.post(
            "/api/login/verify",
            headers=mutation_headers(csrf),
            json={
                "challenge_id": challenge.json()["challenge_id"],
                "signature": SIGNATURE,
            },
        )
        assert verified.status_code == 200
        assert verified.json()["authenticated"] is True
        assert service.login_challenges[0][1] == OWNER


def test_status_is_a_read_dispatch_with_no_csrf_and_tenant_isolation():
    service = FakeHostedService()
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as first, TestClient(
        app, base_url=ORIGIN
    ) as second:
        session(first)
        session(second)
        one = first.get("/api/status")
        two = second.get("/api/status")
        assert one.status_code == two.status_code == 200
        assert one.json()["tenant"] != two.json()["tenant"]
        status_calls = [call for call in service.calls if call[1] == "status"]
        assert [call[2] for call in status_calls] == [None, None]


def test_operation_routes_dispatch_fixed_operation_names_and_forbid_token_output():
    service = FakeHostedService()
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = session(client)
        headers = mutation_headers(csrf)
        cases = [
            ("post", "/api/grant/challenge", {}, "grant_challenge"),
            ("post", "/api/grant/verify", {"signature": SIGNATURE}, "grant_verify"),
            ("post", "/api/budget/payload", {}, "budget_payload"),
            ("post", "/api/budget/bind", {"signature": SIGNATURE}, "budget_bind"),
            ("get", "/api/allowance/transaction", None, "approval_transaction"),
            ("post", "/api/allowance/verify", {"transaction_hash": TX_HASH}, "verify_approval"),
            ("get", "/api/faucet/transaction", None, "claim_transaction"),
            ("post", "/api/faucet/verify", {"transaction_hash": TX_HASH}, "verify_claim"),
            ("post", "/api/opc/prepare", {}, "opc_prepare"),
            ("post", "/api/opc/approve", {"challenge_id": "challenge-1", "signature": SIGNATURE}, "opc_approve"),
            ("post", "/api/preview", {"csv_text": "a,b\n1,2", "idempotency_key": "id-1"}, "preview"),
            ("post", "/api/execute", {"preview_id": "preview-1"}, "execute"),
            ("get", "/api/purchases/purchase-1", None, "purchase"),
            ("post", "/api/purchases/purchase-1/recover", {}, "recover_purchase"),
            ("post", "/api/revoke/prepare", {}, "revoke_prepare"),
            ("post", "/api/revoke/verify", {"transaction_hash": TX_HASH}, "verify_revocation"),
        ]
        for method, path, body, operation in cases:
            kwargs = {"headers": headers}
            if method == "post":
                kwargs["json"] = body
            response = getattr(client, method)(path, **kwargs)
            assert response.status_code == 200, (path, response.text)
            assert service.calls[-1][1] == operation


def test_preview_rejects_unknown_fields_and_transaction_inputs_are_typed():
    app, _ = app_for()
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = session(client)
        headers = mutation_headers(csrf)
        unknown = client.post(
            "/api/preview",
            headers=headers,
            json={"csv_text": "x", "idempotency_key": "i", "owner": OWNER},
        )
        bad_tx = client.post(
            "/api/allowance/verify",
            headers=headers,
            json={"transaction_hash": "not-a-hash"},
        )
        bad_sig = client.post(
            "/api/grant/verify",
            headers=headers,
            json={"signature": "not-a-signature"},
        )
        assert [r.status_code for r in (unknown, bad_tx, bad_sig)] == [422, 422, 422]
        assert OWNER not in unknown.text


def test_opc_token_exchange_result_is_never_returned_to_browser():
    service = FakeHostedService()
    service.return_opc_token = True
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = session(client)
        response = client.post(
            "/api/opc/prepare",
            headers=mutation_headers(csrf),
            json={},
        )
        assert response.status_code == 409
        assert response.json() == {"error": "operation_unconfirmed"}
        assert "private-opc-token" not in response.text


def test_permission_errors_from_dispatch_and_direct_are_sanitized_as_401():
    service = FakeHostedService()
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = session(client)
        service.raise_permission = True
        read = client.get("/api/status")
        direct = client.post(
            "/api/login/challenge",
            headers=mutation_headers(csrf),
            json={"owner": OWNER},
        )

        assert read.status_code == direct.status_code == 401
        assert read.json() == direct.json() == {"error": "authentication_required"}


@pytest.mark.parametrize(
    "secret_key",
    [
        "accessToken",
        "RefreshToken",
        "privateKey",
        "secret",
        "Authorization",
        "AWSAccessKeyId",
        "AWSSecretAccessKey",
        "AWSSessionToken",
    ],
)
def test_response_filter_rejects_normalized_credential_key_aliases(secret_key: str):
    service = FakeHostedService()
    service.return_response = {secret_key: "credential-marker"}
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        session(client)
        response = client.get("/api/status")

        assert response.status_code == 409
        assert response.json() == {"error": "operation_unconfirmed"}
        assert "credential-marker" not in response.text


def test_response_filter_allows_public_token_and_address_fields():
    service = FakeHostedService()
    service.return_response = {
        "publicToken": "public-catalog-token",
        "address": OWNER,
    }
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        session(client)
        response = client.get("/api/status")

        assert response.status_code == 200
        assert response.json() == service.return_response


def test_early_boundary_errors_include_security_headers():
    app, _ = app_for()
    with TestClient(app, base_url=ORIGIN) as client:
        responses = [
            client.get("/api/status"),
            client.post(
                "/api/session",
                headers={"Origin": ORIGIN, "Host": "attacker.example"},
                json={},
            ),
            client.post(
                "/api/session",
                headers={"Origin": "https://attacker.example"},
                json={},
            ),
            client.post(
                "/api/session",
                headers={"Origin": ORIGIN},
                content=b"x" * 262145,
            ),
        ]

        for response in responses:
            assert response.status_code in {401, 403, 413}
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert "frame-ancestors 'none'" in response.headers[
                "content-security-policy"
            ]


def test_request_body_read_has_a_deadline(monkeypatch: pytest.MonkeyPatch):
    from examples.monad_commerce import hosted_api
    from starlette.requests import Request

    async def slow_stream(_request: Request):
        await asyncio.sleep(0.05)
        yield b"{}"

    monkeypatch.setattr(hosted_api, "_REQUEST_BODY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(Request, "stream", slow_stream)
    app, _ = app_for()
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/api/session", headers={"Origin": ORIGIN}, json={}
        )

    assert response.status_code == 408
    assert response.json() == {"error": "request_timeout"}
    assert response.headers["cache-control"] == "no-store"


def test_backend_errors_are_generic_conflicts_and_do_not_leak_exception_text():
    service = FakeHostedService()
    app, _ = app_for(service)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = session(client)
        service.raise_secret = "private AWS credential and stack trace"
        response = client.post(
            "/api/grant/challenge", headers=mutation_headers(csrf), json={}
        )
        assert response.status_code == 409
        assert response.json() == {"error": "operation_unconfirmed"}
        assert "private AWS" not in response.text
        assert "Traceback" not in response.text


def test_static_paths_are_fixed_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from examples.monad_commerce import hosted_api

    static = tmp_path / "hosted_web"
    static.mkdir()
    (static / "index.html").write_text("hosted")
    (static / "app.js").write_text("app")
    (static / "app.css").write_text("css")
    monkeypatch.setattr(hosted_api, "STATIC", static)
    app, _ = app_for()
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/").text == "hosted"
        assert client.get("/app.js").text == "app"
        assert client.get("/app.css").text == "css"


def test_no_forwarded_host_or_origin_trust():
    app, _ = app_for()
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/api/session",
            headers={
                "Origin": ORIGIN,
                "Host": "attacker.example",
                "X-Forwarded-Host": "commerce.example",
            },
            json={},
        )
        assert response.status_code == 403
