"""Loopback operator UI for real Monad testnet; never binds a public interface."""
from contextlib import asynccontextmanager
import argparse
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

from examples.commerce.node import public_result
from examples.monad_commerce.rehearsal import public_purchase

STATIC = Path(__file__).with_name('public_web')


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Signature(Empty):
    signature: str = Field(pattern=r'^0x[0-9a-fA-F]{130}$')


class Transaction(Empty):
    transaction_hash: str = Field(pattern=r'^0x[0-9a-fA-F]{64}$')


class Preview(Empty):
    csv_text: str = Field(min_length=1, max_length=131072)
    idempotency_key: str = Field(min_length=1, max_length=160)


class Execute(Empty):
    preview_id: str = Field(min_length=1, max_length=160)


def create_app(*, origin='http://127.0.0.1:8091', runtime_factory):
    parsed = urlsplit(origin)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
        or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('wallet operator UI must use a loopback origin')
    session_digests = set()
    lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(app):
        with runtime_factory() as runtime:
            app.state.runtime = runtime
            yield

    app = FastAPI(title='Agentonomy Monad wallet acceptance', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def boundary(request: Request, next_handler):
        if request.headers.get('host') != parsed.netloc:
            return JSONResponse({'error': 'invalid_host'}, status_code=403)
        if request.method not in {'GET', 'HEAD'}:
            if request.headers.get('origin') != origin:
                return JSONResponse({'error': 'origin_required'}, status_code=403)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 262144:
                    return JSONResponse({'error': 'body_too_large'}, status_code=413)
            request._body = bytes(body)
        if request.url.path.startswith('/api/') and request.url.path != '/api/session':
            token = request.cookies.get('agentonomy_monad_operator', '')
            digest = hashlib.sha256(token.encode()).hexdigest()
            if not token or digest not in session_digests:
                return JSONResponse({'error': 'session_required'}, status_code=401)
        response = await next_handler(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        return response

    def invoke(method, *args, **kwargs):
        with lock:
            try:
                return getattr(app.state.runtime, method)(*args, **kwargs)
            except (ValueError, RuntimeError, KeyError, OSError):
                raise HTTPException(409, '操作尚未完成，请查看当前授权或原订单状态后重试。') from None

    @app.post('/api/session')
    def session(body: Empty, request: Request):
        with lock:
            prior = request.cookies.get('agentonomy_monad_operator', '')
            if prior and hashlib.sha256(prior.encode()).hexdigest() in session_digests:
                return {'status': 'ready'}
            if len(session_digests) >= 8:
                raise HTTPException(429, '请使用已打开的操作页面。')
            token = secrets.token_urlsafe(32)
            session_digests.add(hashlib.sha256(token.encode()).hexdigest())
        response = JSONResponse({'status': 'ready'})
        response.set_cookie('agentonomy_monad_operator', token, httponly=True,
                            samesite='strict', secure=False, max_age=3600, path='/api')
        return response

    @app.get('/')
    def index(): return FileResponse(STATIC / 'index.html')
    @app.get('/app.js')
    def javascript(): return FileResponse(STATIC / 'app.js', media_type='text/javascript')
    @app.get('/app.css')
    def stylesheet(): return FileResponse(STATIC / 'app.css', media_type='text/css')
    @app.get('/api/status')
    def status(): return invoke('status')
    @app.get('/api/services')
    def search(query: str = ''):
        return public_result('search_clink_services', invoke('request', 'search', {'query': query}))
    @app.get('/api/services/{offering_id}')
    def details(offering_id: str):
        return public_result('get_clink_service_details', invoke('request', 'details', {'offering_id': offering_id}))
    @app.post('/api/wallet/challenge')
    def wallet_challenge(body: Empty): return invoke('onboarding', 'wallet_challenge')
    @app.post('/api/wallet/verify')
    def wallet_verify(body: Signature): return invoke('onboarding', 'wallet_verify', **body.model_dump())
    @app.post('/api/grant/challenge')
    def grant_challenge(body: Empty): return invoke('onboarding', 'grant_challenge')
    @app.post('/api/grant/verify')
    def grant_verify(body: Signature): return invoke('onboarding', 'grant_verify', **body.model_dump())
    @app.get('/api/budget/payload')
    def budget_payload(): return invoke('onboarding', 'budget_payload')
    @app.post('/api/budget/bind')
    def budget_bind(body: Signature): return invoke('onboarding', 'budget_bind', **body.model_dump())
    @app.get('/api/allowance/transaction')
    def allowance_transaction(): return invoke('approval_transaction')
    @app.post('/api/allowance/verify')
    def allowance_verify(body: Transaction): return invoke('verify_approval', body.transaction_hash)
    @app.post('/api/preview')
    def preview(body: Preview):
        value = invoke('request', 'preview', {'offering_id': 'csv-reconciliation-v1', **body.model_dump()})
        return public_result('create_clink_purchase_preview', value)
    @app.post('/api/execute')
    def execute(body: Execute): return public_purchase(invoke('request', 'execute', body.model_dump()))
    @app.get('/api/purchases/{purchase_id}')
    def purchase(purchase_id: str): return public_purchase(invoke('request', 'purchase', {'purchase_id': purchase_id}))
    @app.post('/api/purchases/{purchase_id}/recover')
    def recover_purchase(purchase_id: str, body: Empty):
        return public_purchase(invoke('request', 'recover_purchase', {'purchase_id': purchase_id}))
    @app.post('/api/revoke/prepare')
    def revoke_prepare(body: Empty): return invoke('revoke_prepare')
    @app.post('/api/revoke/verify')
    def revoke_verify(body: Transaction): return invoke('verify_revocation', body.transaction_hash)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8091)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('use an unprivileged local port')
    from examples.monad_commerce.public_canary import PublicCanary
    config = json.loads(args.config.read_text())
    app = create_app(origin=f'http://127.0.0.1:{args.port}',
                     runtime_factory=lambda: PublicCanary(args.state_dir, config))
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
