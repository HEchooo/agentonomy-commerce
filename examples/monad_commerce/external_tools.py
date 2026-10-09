"""The existing commerce state machine exposed to an authenticated device."""
import asyncio
from copy import deepcopy

from mcp import types
from apps.node.clink_node.mcp_proxy import McpToolProxy, NativeTool
from examples.commerce.node import object_schema, IDENTIFIER
from examples.monad_commerce.node import TOOLS


def device_proxy(service, access_token):
    definitions = deepcopy(TOOLS)
    definitions['get_clink_account_readiness'] = (
        'status', 'Read your wallet binding, budget, payment readiness and service identity.',
        object_schema({}),
    )
    definitions['recover_clink_purchase'] = (
        'recover_purchase', 'Recover delivery of the original paid order. Never create another payment.',
        object_schema({'purchase_id': IDENTIFIER,
                       'csv_text': {'type': 'string', 'minLength': 1, 'maxLength': 131072}}, ['purchase_id']),
    )
    descriptions = {
        'get_clink_service_details': 'Read the fixed service price and configured payment scope.',
        'create_clink_purchase_preview': 'Freeze the service input and quote under your signed spending grant.',
        'execute_clink_purchase': 'Execute the original preview under Core authority and return independently verified payment and delivery. If outcome is unknown, query the original purchase; do not create a replacement.',
    }
    native = {}
    for name, (operation, description, schema) in definitions.items():
        async def invoke(arguments, operation=operation):
            return await asyncio.to_thread(service.device_operation, access_token, operation, arguments)
        native[name] = NativeTool(types.Tool(name=name, description=descriptions.get(name, description),
                                             inputSchema=schema), invoke)
    return McpToolProxy({}, native_tools=native)
