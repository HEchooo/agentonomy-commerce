"""Authenticated HTTP facade for the persistent Commerce review sandbox."""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
import time
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agentonomy_commerce.settings import Settings
from examples.commerce.node import MarketplaceBridge, public_result


MAX_BODY_BYTES = 262144
CALL_TIMEOUT_SECONDS = 50
MODE = {"real_funds": False, "settlement_mode": "simulated", "service_transport": "http"}
IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class ReviewHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([(b"cache-control", b"no-store"),
                                (b"x-content-type-options", b"nosniff"),
                                (b"referrer-policy", b"no-referrer"),
                                (b"x-frame-options", b"DENY")])
                if scope.get("path") == "/" or scope.get("path", "").startswith("/assets/"):
                    headers.append((b"content-security-policy", b"default-src 'self'; script-src 'self'; "
                                    b"style-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                                    b"base-uri 'none'; form-action 'none'; object-src 'none'"))
                message = {**message, "headers": headers}
            await send(message)
        await self.app(scope, receive, secured_send)


class ReviewLimits:
    """Authenticate before buffering input, then bound size and request rate."""

    def __init__(self, app, *, settings):
        self.app = app
        self.settings = settings
        self.arrivals = deque()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"authorization", b"")
        expected = b"Bearer " + self.settings.api_token.encode("ascii")
        if not hmac.compare_digest(supplied, expected):
            return await JSONResponse({"error": "unauthorized"}, status_code=401,
                                      headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        now = time.monotonic()
        while self.arrivals and self.arrivals[0] <= now - 60:
            self.arrivals.popleft()
        if len(self.arrivals) >= self.settings.requests_per_minute:
            return await JSONResponse({"error": "rate_limited"}, status_code=429,
                                      headers={"Retry-After": "60"})(scope, receive, send)
        self.arrivals.append(now)
        body = bytearray()
        body_deadline = time.monotonic() + 10
        while True:
            try:
                message = await asyncio.wait_for(receive(), timeout=max(0, body_deadline - time.monotonic()))
            except TimeoutError:
                return await JSONResponse({"error": "request_timeout"}, status_code=408)(scope, receive, send)
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_BODY_BYTES:
                return await JSONResponse({"error": "request_too_large"}, status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offering_id: str = Field(min_length=1, max_length=160)
    csv_text: str = Field(min_length=1, max_length=131072)


class ExecuteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(pattern=r"^preview_[a-zA-Z0-9]+$", max_length=160)


def create_app(settings: Settings | None = None, *, bridge_factory=MarketplaceBridge):
    settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app):
        settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        bridge = bridge_factory(settings.state_dir, worker_module="agentonomy_commerce.worker",
                                worker_args=(str(settings.merchant_port),))
        app.state.bridge = bridge
        app.state.operation_lock = asyncio.Lock()
        app.state.ready = False
        try:
            initial = await asyncio.to_thread(bridge.request, "snapshot")
            if "_error" in initial:
                raise RuntimeError("review worker did not initialize")
            app.state.ready = True
            yield
        finally:
            app.state.ready = False
            await asyncio.to_thread(bridge.close)

    app = FastAPI(title="Agentonomy Commerce", version="0.2.0", lifespan=lifespan,
                  description="Purchase an actual CSV reconciliation report under a signed sandbox budget. "
                              "HTTP service delivery is real; settlement is simulated and no real funds are spent.")
    app.add_middleware(ReviewLimits, settings=settings)
    app.add_middleware(ReviewHeaders)
    static_dir = Path(__file__).with_name("static")
    app.mount("/assets", StaticFiles(directory=static_dir), name="assets")
    secured = [Depends(HTTPBearer(auto_error=False))]

    @app.get("/", include_in_schema=False)
    async def review_page():
        return FileResponse(static_dir / "index.html")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        return JSONResponse({"error": "invalid_request", "fields": [
            {"location": list(item["loc"]), "type": item["type"]} for item in exc.errors()
        ]}, status_code=422)

    async def call(method, arguments=None):
        if not app.state.ready:
            raise HTTPException(503, detail="worker_unavailable")
        try:
            await asyncio.wait_for(app.state.operation_lock.acquire(), timeout=2)
        except TimeoutError as exc:
            raise HTTPException(429, detail="operation_in_progress; read the existing purchase") from exc
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(app.state.bridge.request, method, arguments), timeout=CALL_TIMEOUT_SECONDS)
        except (RuntimeError, OSError, ValueError) as exc:
            app.state.ready = False
            process = getattr(app.state.bridge, "process", None)
            if process is not None and process.poll() is None:
                process.terminate()
            await asyncio.to_thread(app.state.bridge.close)
            raise HTTPException(503, detail="worker_unavailable; inspect the existing purchase before retrying") from exc
        finally:
            app.state.operation_lock.release()
        if "_error" in result:
            code = result["_error"]
            status = {"not_found": 404, "invalid_input": 422, "idempotency_conflict": 409}.get(code, 503)
            raise HTTPException(status, detail=code)
        return result

    @app.get("/health", tags=["verification"])
    async def health():
        ready = getattr(app.state, "ready", False)
        bridge = getattr(app.state, "bridge", None)
        process = getattr(bridge, "process", None)
        if getattr(bridge, "broken", False) or (process is not None and process.poll() is not None):
            ready = False
            app.state.ready = False
        return JSONResponse({"status": "ok" if ready else "unavailable", "commit": settings.source_commit,
                             **MODE}, status_code=200 if ready else 503)

    @app.get("/.well-known/xagent-verification.json", tags=["verification"])
    async def proof():
        return {"schemaVersion": 1, "slug": settings.project_slug, "commit": settings.source_commit}

    @app.get("/v1/services", dependencies=secured, tags=["commerce"])
    async def services():
        return {**await call("search"), **MODE}

    @app.get("/v1/budget", dependencies=secured, tags=["commerce"])
    async def budget():
        return {**await call("snapshot"), **MODE}

    @app.post("/v1/previews", dependencies=secured, tags=["commerce"],
              description="Freeze CSV input and price. Reusing the same Idempotency-Key preserves the preview; "
                          "changing its input returns 409. Preview expires after 300 seconds. Does not charge.")
    async def preview(body: PreviewInput, idempotency_key: Annotated[str, Header(pattern=IDEMPOTENCY_PATTERN)]):
        result = await call("preview", {**body.model_dump(), "idempotency_key": idempotency_key})
        return {**public_result("create_clink_purchase_preview", result), **MODE}

    @app.post("/v1/purchases", dependencies=secured, tags=["commerce"],
              description="Consumes 0.30 sandbox USDC from the existing signed budget and calls the HTTP merchant. "
                          "Replay the same preview; never create a new purchase to recover an uncertain outcome.")
    async def execute(body: ExecuteInput):
        result = await call("execute", body.model_dump())
        return {**public_result("execute_clink_purchase", result), **MODE}

    @app.get("/v1/purchases/{purchase_id}", dependencies=secured, tags=["commerce"],
             description="Read persisted order and retained result without charging; results expire after 7 days.")
    async def purchase(purchase_id: str):
        if len(purchase_id) > 160:
            raise HTTPException(404, detail="not_found")
        result = await call("purchase", {"purchase_id": purchase_id})
        return {**public_result("get_clink_purchase", result), **MODE}

    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("AGENTONOMY_HOST", "127.0.0.1"),
                port=int(os.environ.get("AGENTONOMY_PORT", "8080")))
