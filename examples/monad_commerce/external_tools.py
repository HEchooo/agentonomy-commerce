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
    definitions['prepare_clink_purchase_feedback'] = (
        'feedback_prepare', 'Prepare an immutable buyer rating for your verified delivered order. Ask the user for an explicit integer score 0..100; never invent it. Open the returned wallet consent link for disclosure and signing. This tool never sends a transaction.',
        object_schema({'purchase_id': IDENTIFIER, 'score': {'type': 'integer', 'minimum': 0, 'maximum': 100}}, ['purchase_id', 'score']),
    )
    definitions['get_clink_purchase_feedback'] = (
        'feedback_status', 'Read and independently verify the original buyer feedback, including current revocation state. Never submit another feedback transaction when pending.',
        object_schema({'purchase_id': IDENTIFIER}, ['purchase_id']),
    )
    definitions['verify_clink_purchase_feedback'] = (
        'feedback_verify', 'Verify an existing wallet feedback transaction for your original delivered purchase; this does not sign or send a transaction.',
        object_schema({'purchase_id': IDENTIFIER, 'transaction_hash': {'type': 'string', 'pattern': r'^0x[0-9a-fA-F]{64}$'}}, ['purchase_id', 'transaction_hash']),
    )
    definitions['get_clink_service_history'] = (
        'service_history', 'Read verified public service history shared across buyers: delivered payments and optional buyer ratings are separate. Report sample size and verification freshness; no history means no rating. One pilot service is configured, so do not claim competing merchants or an established market.',
        object_schema({'offering_id': IDENTIFIER}, ['offering_id']),
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
