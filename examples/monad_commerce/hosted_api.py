"""HTTPS browser boundary for the hosted Agentonomy Commerce demo.

The hosted service owns identity, authorization, and all commerce state.  This
module is deliberately a thin HTTP adapter: browser credentials are generated
here, but are passed to the service for validation and never exposed as API
tokens.  The service factory is context managed so the application cannot
accidentally outlive its signing/runtime resources.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import re
import secrets
import threading
from typing import Any, Mapping
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field


STATIC = Path(__file__).with_name("hosted_web")

SESSION_COOKIE = "agentonomy_hosted_session"
CSRF_COOKIE = "agentonomy_hosted_csrf"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
OWNER_PATTERN = r"^0x[0-9a-fA-F]{40}$"
SIGNATURE_PATTERN = r"^0x[0-9a-fA-F]{130}$"
TRANSACTION_PATTERN = r"^0x[0-9a-fA-F]{64}$"
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$"

_NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    ),
}
_SECRET_RESPONSE_KEYS = {
    "access_token",
    "browser_token",
    "csrf_token",
    "opc_access_token",
    "opc_token",
    "refresh_token",
    "private_key",
    "secret",
    "authorization",
    "credential",
    "credentials",
    "api_key",
    "api_secret",
    "client_secret",
    "password",
    "aws_access_key",
    "aws_access_key_id",
    "aws_secret_key",
    "aws_secret_access_key",
    "aws_session_token",
    "aws_security_token",
    "aws_credentials",
}
_REQUEST_BODY_LIMIT = 262144
_REQUEST_BODY_TIMEOUT_SECONDS = 10.0


class _AuthenticationRequired(Exception):
    """Internal control flow for a sanitized 401 response."""


class _OperationUnconfirmed(Exception):
    """Internal control flow for a sanitized 409 response."""


class _CsrfRequired(Exception):
    """Internal control flow for a sanitized CSRF failure."""


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Owner(Empty):
    owner: str = Field(pattern=OWNER_PATTERN)


class Signature(Empty):
    signature: str = Field(pattern=SIGNATURE_PATTERN)


class Transaction(Empty):
    transaction_hash: str = Field(pattern=TRANSACTION_PATTERN)


class LoginVerify(Empty):
    challenge_id: str = Field(pattern=ID_PATTERN)
    signature: str = Field(pattern=SIGNATURE_PATTERN)


class OpcPrepare(Empty):
    """The hosted service supplies the device proof server-side."""


class OpcApprove(Empty):
    challenge_id: str = Field(pattern=ID_PATTERN)
    signature: str = Field(pattern=SIGNATURE_PATTERN)


class Preview(Empty):
    csv_text: str = Field(min_length=1, max_length=131072)
    idempotency_key: str = Field(min_length=1, max_length=160)


class Execute(Empty):
    preview_id: str = Field(pattern=ID_PATTERN)


class Recover(Empty):
    csv_text: str | None = Field(default=None, min_length=1, max_length=131072)


class FeedbackScore(Empty):
    score: int = Field(strict=True, ge=0, le=100)


def _origin_parts(origin: str, *, allow_loopback_http: bool) -> tuple[str, str, bool]:
    """Validate an origin and return ``(origin, host, secure_cookie)``."""

    parsed = urlsplit(origin)
    if (
        parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not parsed.netloc
        or parsed.hostname is None
    ):
        raise ValueError("origin must be an absolute origin without a path")
    if parsed.scheme == "https":
        return origin, parsed.netloc, True
    if (
        parsed.scheme == "http"
        and allow_loopback_http
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        return origin, parsed.netloc, False
    raise ValueError("hosted API requires HTTPS; HTTP is limited to loopback tests")


def _single_header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    if len(values) != 1:
        return None
    value = values[0].strip()
    return value or None


def _valid_token(value: str | None) -> bool:
    return bool(value and TOKEN_PATTERN.fullmatch(value))


def _normalize_response_key(key: object) -> str:
    value = str(key).replace("-", "_").replace(" ", "_")
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_").lower()


def _safe_response(value: Any, *, reject_token: bool = False) -> Any:
    """Reject accidental raw credentials returned by a backend method."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            key_name = _normalize_response_key(key)
            if key_name in _SECRET_RESPONSE_KEYS or (
                reject_token and key_name == "token"
            ):
                raise _OperationUnconfirmed
            _safe_response(item, reject_token=reject_token)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _safe_response(item, reject_token=reject_token)
    return value


def _security_response(response: JSONResponse) -> JSONResponse:
    for key, value in _NO_STORE_HEADERS.items():
        response.headers[key] = value
    return response


def _error_response(payload: Mapping[str, str], status_code: int) -> JSONResponse:
    return _security_response(JSONResponse(payload, status_code=status_code))


def create_app(
    *,
    origin: str,
    runtime_factory,
    allow_loopback_http: bool = False,
) -> FastAPI:
    """Create the hosted browser API around a context-managed service.

    ``origin`` is also the exact Host/Origin admission value.  The optional
    loopback flag exists only for local tests and development; public hosted
    instances must use an HTTPS origin.
    """

    expected_origin, expected_host, secure_cookie = _origin_parts(
        origin, allow_loopback_http=allow_loopback_http
    )
    lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with runtime_factory() as service:
            app.state.hosted_service = service
            yield

    app = FastAPI(
        title="Agentonomy Commerce hosted wallet",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(_AuthenticationRequired)
    async def authentication_error(_request: Request, _exc: _AuthenticationRequired):
        return _error_response({"error": "authentication_required"}, 401)

    @app.exception_handler(_OperationUnconfirmed)
    async def operation_error(_request: Request, _exc: _OperationUnconfirmed):
        return _error_response({"error": "operation_unconfirmed"}, 409)

    @app.exception_handler(_CsrfRequired)
    async def csrf_error(_request: Request, _exc: _CsrfRequired):
        return _error_response({"error": "csrf_required"}, 403)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError):
        # Never echo submitted signatures, wallet addresses, or proof strings.
        return _error_response({"error": "invalid_request"}, 422)

    @app.middleware("http")
    async def boundary(request: Request, next_handler):
        if _single_header(request, "host") != expected_host:
            return _error_response({"error": "invalid_host"}, 403)

        mutating = request.method not in {"GET", "HEAD"}
        if mutating:
            if _single_header(request, "origin") != expected_origin:
                return _error_response({"error": "origin_required"}, 403)
            body = bytearray()
            try:
                async with asyncio.timeout(_REQUEST_BODY_TIMEOUT_SECONDS):
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > _REQUEST_BODY_LIMIT:
                            return _error_response({"error": "body_too_large"}, 413)
            except TimeoutError:
                return _error_response({"error": "request_timeout"}, 408)
            request._body = bytes(body)

        if request.url.path.startswith("/api") and request.url.path != "/api/session":
            if not _valid_token(request.cookies.get(SESSION_COOKIE)):
                return _error_response({"error": "authentication_required"}, 401)

        response = await next_handler(request)
        return _security_response(response)

    def service():
        try:
            return app.state.hosted_service
        except AttributeError:
            raise _OperationUnconfirmed from None

    def session_tokens(request: Request, *, mutating: bool) -> tuple[str, str | None]:
        browser_token = request.cookies.get(SESSION_COOKIE)
        if not _valid_token(browser_token):
            raise _AuthenticationRequired
        csrf_token: str | None = None
        if mutating:
            csrf_cookie = request.cookies.get(CSRF_COOKIE)
            csrf_header = _single_header(request, "x-agentonomy-csrf")
            if not _valid_token(csrf_cookie) or csrf_header != csrf_cookie:
                raise _CsrfRequired
            csrf_token = csrf_cookie
        try:
            with lock:
                value = service().session(browser_token, csrf_token)
        except _OperationUnconfirmed:
            raise
        except Exception:
            raise _AuthenticationRequired from None
        if not isinstance(value, Mapping):
            raise _AuthenticationRequired
        return browser_token, csrf_token

    def dispatch(request: Request, operation: str, args: dict[str, object], *, mutating: bool):
        browser_token, csrf_token = session_tokens(request, mutating=mutating)
        try:
            with lock:
                value = service().dispatch(browser_token, csrf_token, operation, args)
            if not isinstance(value, Mapping):
                raise _OperationUnconfirmed
            _safe_response(value, reject_token=operation.startswith("opc_"))
            return dict(value)
        except PermissionError:
            raise _AuthenticationRequired from None
        except (_AuthenticationRequired, _OperationUnconfirmed):
            raise
        except Exception:
            raise _OperationUnconfirmed from None

    def direct(request: Request, method: str, *args):
        browser_token, csrf_token = session_tokens(request, mutating=True)
        try:
            with lock:
                value = getattr(service(), method)(browser_token, csrf_token, *args)
            if not isinstance(value, Mapping):
                raise _OperationUnconfirmed
            _safe_response(value)
            return dict(value)
        except PermissionError:
            raise _AuthenticationRequired from None
        except (_AuthenticationRequired, _OperationUnconfirmed):
            raise
        except Exception:
            raise _OperationUnconfirmed from None

    @app.post("/api/session")
    def session(_body: Empty, request: Request):
        browser_token = request.cookies.get(SESSION_COOKIE)
        csrf_token = request.cookies.get(CSRF_COOKIE)
        value: Mapping[str, object] | None = None
        if _valid_token(browser_token) and _valid_token(csrf_token):
            try:
                with lock:
                    value = service().session(browser_token, csrf_token)
            except PermissionError:
                browser_token = None
                csrf_token = None
                value = None
            except _OperationUnconfirmed:
                raise
            except Exception:
                raise _OperationUnconfirmed from None
            if value is not None and not isinstance(value, Mapping):
                raise _OperationUnconfirmed
            if value is not None:
                _safe_response(value, reject_token=True)

        if value is None:
            browser_token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            try:
                with lock:
                    value = service().session(browser_token, csrf_token)
                if not isinstance(value, Mapping):
                    raise _OperationUnconfirmed
                _safe_response(value, reject_token=True)
            except _OperationUnconfirmed:
                raise
            except Exception:
                raise _OperationUnconfirmed from None

        if (
            not _valid_token(browser_token)
            or not _valid_token(csrf_token)
            or not isinstance(value, Mapping)
        ):
            raise _OperationUnconfirmed
        payload = dict(value)
        payload["csrf_token"] = csrf_token
        cookie_options = {
            "httponly": True,
            "secure": secure_cookie,
            "samesite": "strict",
            "max_age": 3600,
            "path": "/",
        }
        response = JSONResponse(payload)
        response.set_cookie(SESSION_COOKIE, browser_token, **cookie_options)
        response.set_cookie(
            CSRF_COOKIE,
            csrf_token,
            httponly=False,
            secure=secure_cookie,
            samesite="strict",
            max_age=3600,
            path="/",
        )
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/app.js")
    def javascript():
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @app.get("/app.css")
    def stylesheet():
        return FileResponse(STATIC / "app.css", media_type="text/css")

    @app.get("/agent.svg")
    def agent_image():
        return FileResponse(STATIC / "agent.svg", media_type="image/svg+xml")

    @app.get("/agent.json")
    @app.get("/.well-known/agent-registration.json")
    def agent_registration():
        try:
            with lock:
                value = service().registration_document()
            if not isinstance(value, Mapping):
                raise _OperationUnconfirmed
            return _safe_response(value, reject_token=True)
        except Exception:
            raise _OperationUnconfirmed from None

    @app.get("/erc8004/feedback/{digest}.json")
    def public_feedback(digest: str):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            return _error_response({"error": "not_found"}, 404)
        try:
            with lock:
                value = service().public_feedback('0x' + digest)
            if value is None:
                return _error_response({"error": "not_found"}, 404)
            if not isinstance(value, Mapping):
                raise _OperationUnconfirmed
            return _safe_response(value, reject_token=True)
        except Exception:
            raise _OperationUnconfirmed from None

    @app.get("/api/agent")
    def agent_identity(request: Request):
        return dispatch(request, "agent_identity", {}, mutating=False)

    @app.get("/api/status")
    def status(request: Request):
        return dispatch(request, "status", {}, mutating=False)

    @app.get("/api/services")
    def search(request: Request, query: str = Query(default="", max_length=256)):
        return dispatch(request, "search", {"query": query}, mutating=False)

    @app.get("/api/services/{offering_id}")
    def details(request: Request, offering_id: str):
        if not re.fullmatch(ID_PATTERN, offering_id):
            raise _OperationUnconfirmed
        return dispatch(
            request, "details", {"offering_id": offering_id}, mutating=False
        )

    @app.post("/api/login/challenge")
    def login_challenge(body: Owner, request: Request):
        return direct(request, "login_challenge", body.owner)

    @app.post("/api/login/verify")
    def login_verify(body: LoginVerify, request: Request):
        return direct(request, "login_verify", body.challenge_id, body.signature)

    @app.post("/api/grant/challenge")
    def grant_challenge(request: Request, _body: Empty):
        return dispatch(request, "grant_challenge", {}, mutating=True)

    @app.post("/api/grant/verify")
    def grant_verify(request: Request, body: Signature):
        return dispatch(request, "grant_verify", body.model_dump(), mutating=True)

    @app.post("/api/budget/payload")
    def budget_payload(request: Request, _body: Empty):
        return dispatch(request, "budget_payload", {}, mutating=True)

    @app.post("/api/budget/bind")
    def budget_bind(request: Request, body: Signature):
        return dispatch(request, "budget_bind", body.model_dump(), mutating=True)

    @app.get("/api/allowance/transaction")
    def approval_transaction(request: Request):
        return dispatch(request, "approval_transaction", {}, mutating=False)

    @app.post("/api/allowance/verify")
    def verify_approval(request: Request, body: Transaction):
        return dispatch(request, "verify_approval", body.model_dump(), mutating=True)

    @app.get("/api/faucet/transaction")
    def claim_transaction(request: Request):
        return dispatch(request, "claim_transaction", {}, mutating=False)

    @app.post("/api/faucet/verify")
    def verify_claim(request: Request, body: Transaction):
        return dispatch(request, "verify_claim", body.model_dump(), mutating=True)

    @app.post("/api/opc/prepare")
    def opc_prepare(request: Request, _body: OpcPrepare):
        return dispatch(request, "opc_prepare", {}, mutating=True)

    @app.post("/api/opc/approve")
    def opc_approve(request: Request, body: OpcApprove):
        return dispatch(request, "opc_approve", body.model_dump(), mutating=True)

    @app.post("/api/preview")
    def preview(request: Request, body: Preview):
        return dispatch(
            request,
            "preview",
            {"offering_id": "csv-reconciliation-v1", **body.model_dump()},
            mutating=True,
        )

    @app.post("/api/execute")
    def execute(request: Request, body: Execute):
        return dispatch(request, "execute", body.model_dump(), mutating=True)

    @app.get("/api/purchases/{purchase_id}")
    def purchase(request: Request, purchase_id: str):
        if not re.fullmatch(ID_PATTERN, purchase_id):
            raise _OperationUnconfirmed
        return dispatch(
            request, "purchase", {"purchase_id": purchase_id}, mutating=False
        )

    @app.post("/api/purchases/{purchase_id}/recover")
    def recover_purchase(request: Request, purchase_id: str, body: Recover):
        if not re.fullmatch(ID_PATTERN, purchase_id):
            raise _OperationUnconfirmed
        args = {"purchase_id": purchase_id, **body.model_dump(exclude_none=True)}
        return dispatch(request, "recover_purchase", args, mutating=True)

    @app.post("/api/purchases/{purchase_id}/feedback/prepare")
    def feedback_prepare(request: Request, purchase_id: str, body: FeedbackScore):
        if not re.fullmatch(ID_PATTERN, purchase_id):
            raise _OperationUnconfirmed
        return dispatch(request, "feedback_prepare", {"purchase_id": purchase_id, **body.model_dump()}, mutating=True)

    @app.post("/api/purchases/{purchase_id}/feedback/verify")
    def feedback_verify(request: Request, purchase_id: str, body: Transaction):
        if not re.fullmatch(ID_PATTERN, purchase_id):
            raise _OperationUnconfirmed
        return dispatch(request, "feedback_verify", {"purchase_id": purchase_id, **body.model_dump()}, mutating=True)

    @app.get("/api/purchases/{purchase_id}/feedback")
    def feedback_status(request: Request, purchase_id: str):
        if not re.fullmatch(ID_PATTERN, purchase_id):
            raise _OperationUnconfirmed
        return dispatch(request, "feedback_status", {"purchase_id": purchase_id}, mutating=False)

    @app.post("/api/revoke/prepare")
    def revoke_prepare(request: Request, _body: Empty):
        return dispatch(request, "revoke_prepare", {}, mutating=True)

    @app.post("/api/revoke/verify")
    def verify_revocation(request: Request, body: Transaction):
        return dispatch(request, "verify_revocation", body.model_dump(), mutating=True)

    @app.post("/api/logout")
    def logout(request: Request, _body: Empty):
        value = direct(request, "logout")
        response = JSONResponse(value)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    return app
