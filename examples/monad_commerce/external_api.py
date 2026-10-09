"""Separate browser-cookie and external-device HTTP admission surfaces."""
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.applications import Starlette
from starlette.routing import Mount, Route

# The copied Node imports Core's ``shared`` package. Reuse the standalone
# composition's namespace bootstrap before importing that gateway.
from examples.commerce import node as _node_bootstrap
from apps.node.clink_node.agent_access_http import _bounded_request
from apps.node.clink_node.mcp_gateway import create_mcp_application
from examples.monad_commerce.hosted_mcp import HostedMcpProxy
from examples.monad_commerce.hosted_api import (
    create_app, STATIC, SESSION_COOKIE, CSRF_COOKIE, _valid_token, _single_header,
    _safe_response, _error_response, _security_response,
)


class DeviceProof(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    proof: str = Field(min_length=1, max_length=16384, pattern=r'^[\x21-\x7e]+$')


class DeviceLink(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')


class DeviceConsent(DeviceLink):
    challenge_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$')
    signature: str = Field(pattern=r'^0x[0-9a-fA-F]{130}$')


class MachineBoundary:
    def __init__(self, app, *, origin):
        self.app, self.origin = app, origin
        self.host = urlsplit(origin).netloc.encode('ascii')

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        headers = scope.get('headers', [])
        hosts = [value for name, value in headers if name.lower() == b'host']
        origins = [value for name, value in headers if name.lower() == b'origin']
        if (hosts != [self.host] or len(origins) > 1
            or (origins and origins != [self.origin.encode('ascii')])
            or any(name.lower() == b'cookie' for name, _ in headers)):
            await _error_response({'error': 'machine_admission_required'}, 403)(scope, receive, send)
            return

        async def no_store(message):
            if message['type'] == 'http.response.start':
                message = dict(message)
                message['headers'] = [(name, value) for name, value in message.get('headers', [])
                                      if name.lower() != b'cache-control'] + [(b'cache-control', b'no-store')]
            await send(message)
        await _bounded_request(self.app, scope, receive, no_store)


def create_external_app(*, origin, runtime_factory):
    browser = create_app(origin=origin, runtime_factory=runtime_factory)
    # The legacy in-page Agent composition remains an internal test fixture;
    # this public mode exposes only wallet and authorization management.
    excluded = {'/', '/app.js', '/api/agent', '/api/services', '/api/services/{offering_id}',
                '/api/opc/prepare', '/api/opc/approve', '/api/preview', '/api/execute',
                '/api/purchases/{purchase_id}', '/api/purchases/{purchase_id}/recover',
                '/api/purchases/{purchase_id}/feedback/prepare',
                '/api/purchases/{purchase_id}/feedback/verify', '/api/purchases/{purchase_id}/feedback'}
    browser.router.routes[:] = [route for route in browser.router.routes if getattr(route, 'path', None) not in excluded]

    def service():
        return browser.state.hosted_service

    def browser_tokens(request, *, mutation):
        token = request.cookies.get(SESSION_COOKIE)
        if not _valid_token(token):
            raise PermissionError('wallet browser required')
        csrf = None
        if mutation:
            csrf = request.cookies.get(CSRF_COOKIE)
            if not _valid_token(csrf) or _single_header(request, 'x-agentonomy-csrf') != csrf:
                raise PermissionError('CSRF required')
        return token, csrf

    @browser.get('/')
    def index():
        return FileResponse(STATIC / 'binding.html')

    @browser.get('/account/{request_token}')
    def device_page(request_token: str):
        if not _valid_token(request_token):
            return _error_response({'error': 'not_found'}, 404)
        return FileResponse(STATIC / 'binding.html', headers={'Referrer-Policy': 'no-referrer'})

    @browser.get('/binding.js')
    def binding_script():
        return FileResponse(STATIC / 'binding.js', media_type='text/javascript')

    @browser.get('/binding.css')
    def binding_styles():
        return FileResponse(STATIC / 'binding.css', media_type='text/css')

    @browser.get('/wallet_selection.js')
    def wallets_script():
        return FileResponse(STATIC / 'wallet_selection.js', media_type='text/javascript')

    @browser.get('/wallet-config.json')
    def wallet_config():
        return _safe_response(service().wallet_configuration())

    @browser.get('/api/opc/external')
    def binding_status(request: Request):
        try:
            token, _ = browser_tokens(request, mutation=False)
            return _safe_response(service().binding_status(token, request.query_params.get('request')), reject_token=True)
        except PermissionError:
            return _error_response({'error': 'binding_unavailable'}, 401)
        except Exception:
            return _error_response({'error': 'binding_unconfirmed'}, 409)

    @browser.post('/api/opc/external/claim')
    def claim(body: DeviceLink, request: Request):
        try:
            token, csrf = browser_tokens(request, mutation=True)
        except PermissionError:
            return _error_response({'error': 'csrf_required'}, 403)
        try:
            return _safe_response(service().claim_device(token, csrf, body.request), reject_token=True)
        except PermissionError:
            return _error_response({'error': 'binding_unavailable'}, 401)
        except Exception:
            return _error_response({'error': 'binding_unconfirmed'}, 409)

    @browser.post('/api/opc/external/approve')
    def approve(body: DeviceConsent, request: Request):
        try:
            token, csrf = browser_tokens(request, mutation=True)
        except PermissionError:
            return _error_response({'error': 'csrf_required'}, 403)
        try:
            return _safe_response(service().approve_device(token, csrf, body.request,
                                  body.challenge_id, body.signature), reject_token=True)
        except PermissionError:
            return _error_response({'error': 'binding_unavailable'}, 401)
        except Exception:
            return _error_response({'error': 'binding_unconfirmed'}, 409)

    machine = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)

    @machine.exception_handler(RequestValidationError)
    async def validation_error(_request, _error):
        return _error_response({'error': 'invalid_request'}, 422)

    for endpoint, action in (('/pairings', 'pair'), ('/status', 'status'), ('/token', 'token'), ('/revoke', 'revoke')):
        def handler_for(action):
            def handler(body: DeviceProof):
                try:
                    result = service().device_request(action, body.proof)
                    return JSONResponse(result, status_code=201 if action == 'pair' else 200)
                except PermissionError:
                    return _error_response({'error': 'device_authorization_required'}, 401)
                except ValueError:
                    return _error_response({'error': 'invalid_device_proof'}, 400)
                except Exception:
                    return _error_response({'error': 'device_operation_unconfirmed'}, 409)
            return handler
        machine.add_api_route(endpoint, handler_for(action), methods=['POST'])

    class DeviceAccess:
        def authenticate(self, access_token):
            try:
                return service().authenticate(access_token)
            except PermissionError:
                raise ValueError('device unauthorized') from None

    mcp = create_mcp_application(HostedMcpProxy(), access_service=DeviceAccess())

    @asynccontextmanager
    async def lifespan(app):
        async with browser.router.lifespan_context(browser):
            app.state.hosted_service = service()
            async with mcp.router.lifespan_context(mcp):
                yield

    return Starlette(routes=[
        Mount('/v1/opc', MachineBoundary(machine, origin=origin)),
        Route('/mcp', MachineBoundary(mcp, origin=origin), methods=['GET', 'POST', 'DELETE']),
        Mount('/', browser),
    ], lifespan=lifespan)
