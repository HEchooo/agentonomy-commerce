"""Unified MCP over the authorized loopback API; no AWS SDK or wallet keys."""
import argparse
import asyncio
from urllib.parse import quote, urlsplit

import httpx
from mcp import types
from mcp.server.stdio import stdio_server
from clink_node.mcp_gateway import create_mcp_server
from clink_node.mcp_proxy import McpToolProxy, NativeTool
from examples.commerce.node import object_schema
from examples.monad_commerce.node import BudgetMarketplaceClient, TOOLS


class ApiCanaryClient:
    def __init__(self, origin='http://127.0.0.1:8091', *, transport=None):
        parsed = urlsplit(origin)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('explicit loopback operator API required')
        self.client = httpx.Client(base_url=origin, headers={'Origin': origin},
            timeout=270, trust_env=False, follow_redirects=False, transport=transport)

    def __enter__(self):
        self.client.post('/api/session', json={}).raise_for_status()
        return self

    def __exit__(self, *_): self.client.close()

    def request(self, method, args=None):
        args = args or {}
        if method == 'snapshot':
            response = self.client.get('/api/status')
        elif method == 'search':
            response = self.client.get('/api/services', params=args)
        elif method == 'details' and set(args) == {'offering_id'}:
            response = self.client.get('/api/services/' + quote(args['offering_id'], safe=''))
        elif method == 'preview' and set(args) == {'offering_id','csv_text','idempotency_key'}:
            if args['offering_id'] != 'csv-reconciliation-v1':
                raise ValueError('offering outside canary scope')
            response = self.client.post('/api/preview', json={k:v for k,v in args.items() if k != 'offering_id'})
        elif method == 'execute' and set(args) == {'preview_id'}:
            response = self.client.post('/api/execute', json=args)
        elif method == 'purchase' and set(args) == {'purchase_id'}:
            response = self.client.get('/api/purchases/' + quote(args['purchase_id'], safe=''))
        else:
            raise ValueError('operation outside public MCP scope')
        response.raise_for_status()
        return response.json()

    def execute(self, preview_id):
        return self.request('execute', {'preview_id': preview_id})


class PublicMarketplaceClient(BudgetMarketplaceClient):
    async def list_tools(self):
        return [types.Tool(name=name,
            description=description.replace('local EVM', 'Monad testnet').replace("local user's", "wallet owner's"),
            inputSchema=schema) for name, (_, description, schema) in TOOLS.items()]


async def serve(origin):
    with ApiCanaryClient(origin) as runtime:
        async def status(_):
            return await asyncio.to_thread(runtime.request, 'snapshot')
        native = NativeTool(types.Tool(name='clink_node_status',
            description='Monad testnet wallet setup, budget and verified payment status. TestUSD has no value.',
            inputSchema=object_schema({})), status)
        proxy = McpToolProxy({'marketplace': PublicMarketplaceClient(runtime)},
                             native_tools={'clink_node_status': native})
        server = create_mcp_server(proxy)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', default='http://127.0.0.1:8091')
    asyncio.run(serve(parser.parse_args().origin))


if __name__ == '__main__': main()
