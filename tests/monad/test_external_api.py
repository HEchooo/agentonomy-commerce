import asyncio
import json

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from fastapi.testclient import TestClient

from test_external_opc import DeviceCanary, NOW, proof, active
from test_hosted_service import configuration, ORIGIN, OWNER_A
from shared.hosted_facilitator_protocol import DeviceSigningKey


@pytest.fixture
def app(tmp_path):
    from examples.monad_commerce.external_api import create_external_app
    from examples.monad_commerce.external_opc import ExternalHostedCommerceService
    NOW[0] = 2000000000
    DeviceCanary.devices = {}
    DeviceCanary.tokens = {}
    service = ExternalHostedCommerceService(tmp_path, configuration(), public_origin=ORIGIN,
                                           canary_factory=DeviceCanary, clock=lambda: NOW[0])
    return create_external_app(origin=ORIGIN, runtime_factory=lambda: service)


def test_proof_api_needs_no_browser_cookie_and_rejects_wrong_origin_and_secrets(app):
    key = DeviceSigningKey.generate()
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post('/v1/opc/pairings', json={'proof': proof(key, 'pair')})
        assert response.status_code == 201
        assert response.headers['cache-control'] == 'no-store'
        assert client.cookies == {}
        assert client.post('/v1/opc/token', json={'proof': proof(key, 'token')}).status_code == 401
        assert client.post('/v1/opc/status', json={'proof': proof(key, 'status')},
                           headers={'Origin': 'https://wrong.example'}).status_code == 403
        response = client.post('/v1/opc/status', json={'proof': 'private-marker', 'owner': OWNER_A})
        assert response.status_code == 422 and 'private-marker' not in response.text
        assert client.get('/v1/opc/status').status_code in {404, 405}
        assert client.post('/v1/opc/pairings/', json={'proof': proof(key, 'pair')}, follow_redirects=False).status_code == 404


def test_browser_scope_excludes_business_routes_and_requires_csrf(app):
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get('/wallet-config.json').status_code == 200
        assert client.get('/wallet-config.json').json()['networks'][0]['chain_id'] == 10143
        assert client.post('/api/session', json={}).status_code == 403
        assert client.post('/api/session', json={}, headers={'Origin': ORIGIN}).status_code == 200
        for path in ('/api/preview', '/api/execute', '/api/opc/prepare'):
            assert client.post(path, json={}, headers={'Origin': ORIGIN}).status_code == 404
        assert client.get('/api/services').status_code == 404
        assert client.post('/api/opc/external/claim', json={'request': 'a'*43},
                           headers={'Origin': ORIGIN}).status_code == 403
        assert client.post('/v1/opc/status', json={'proof': 'private-marker'}).status_code == 403


def test_actual_external_mcp_sdk_uses_core_identity_and_rejects_injected_owner(app):
    async def scenario():
        async with app.router.lifespan_context(app):
            service = app.state.hosted_service
            key = DeviceSigningKey.generate()
            active(service, key)
            issued = service.device_request('token', proof(key, 'token'))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                headers={'Authorization': 'Bearer ' + issued['access_token'],
                         'Accept': 'application/json, text/event-stream'}) as client:
                async with streamable_http_client(ORIGIN + '/mcp', http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = (await session.list_tools()).tools
                        assert 'get_clink_account_readiness' in {tool.name for tool in tools}
                        assert 'recover_clink_purchase' in {tool.name for tool in tools}
                        result = await session.call_tool('get_clink_account_readiness', {})
                        assert not result.isError
                        assert result.structuredContent['owner'] == OWNER_A
                        rejected = await session.call_tool('get_clink_account_readiness', {'owner': 'other'})
                        assert rejected.isError
                service.device_request('revoke', proof(key, 'revoke'))
                response = await client.post('/mcp', json={})
                assert response.status_code == 401
    asyncio.run(scenario())


def test_actual_mcp_preview_injects_device_and_projects_public_response(app):
    async def scenario():
        async with app.router.lifespan_context(app):
            service = app.state.hosted_service
            key = DeviceSigningKey.generate()
            active(service, key)
            issued = service.device_request('token', proof(key, 'token'))
            _, canary = service._credential(issued['access_token'])
            calls = []
            def request(operation, arguments):
                calls.append((operation, arguments))
                return {'preview_id': 'preview_a', 'state': 'ready',
                        'user_id': 'internal-user', 'opc_installation_id': 'internal-device'}
            canary.request = request
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    headers={'Authorization': 'Bearer ' + issued['access_token']}) as client:
                async with streamable_http_client(ORIGIN + '/mcp', http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        arguments = {'offering_id': 'csv-reconciliation-v1', 'csv_text': 'csv',
                                     'idempotency_key': 'preview-request'}
                        result = await session.call_tool('create_clink_purchase_preview', arguments)
                        assert not result.isError
                        assert result.structuredContent == {'preview_id': 'preview_a', 'state': 'ready'}
                        assert calls[0][1]['opc_installation_id'] == issued['installation_id']
                        rejected = await session.call_tool('create_clink_purchase_preview',
                                                          arguments | {'opc_installation_id': 'other-device'})
                        assert rejected.isError and len(calls) == 1
    asyncio.run(scenario())
