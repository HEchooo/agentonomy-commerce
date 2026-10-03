"""Unified clink_node MCP running the actual local budget contract loop."""
import asyncio
import json
from mcp import types
from mcp.server.stdio import stdio_server
from clink_node.mcp_gateway import create_mcp_server
from clink_node.mcp_proxy import McpToolProxy, NativeTool
from examples.commerce.node import object_schema, IDENTIFIER, public_result
from examples.monad_commerce.rehearsal import LocalRehearsal, public_purchase

TOOLS = {
    "search_clink_services": ("search", "Find the first-party CSV reconciliation service.", object_schema({"query":{"type":"string","maxLength":200}})),
    "get_clink_service_details": ("details", "Read price and TestUSD payment scope.", object_schema({"offering_id":IDENTIFIER}, ["offering_id"])),
    "create_clink_purchase_preview": ("preview", "Freeze input and a 0.30 TestUSD quote under the local user's grant.", object_schema({
        "offering_id":IDENTIFIER, "csv_text":{"type":"string","maxLength":131072}, "idempotency_key":IDENTIFIER
    }, ["offering_id","csv_text","idempotency_key"])),
    "execute_clink_purchase": ("execute", "Pay via the local EVM budget contract, verify payment and retrieve the report. Retry the same preview on pending; never create a replacement.", object_schema({"preview_id":IDENTIFIER}, ["preview_id"])),
    "get_clink_purchase": ("purchase", "Read an existing purchase and result without paying again.", object_schema({"purchase_id":IDENTIFIER}, ["purchase_id"])),
}


class BudgetMarketplaceClient:
    def __init__(self, runtime):
        self.runtime = runtime

    async def list_tools(self):
        return [types.Tool(name=name, description=description, inputSchema=schema)
                for name, (_,description,schema) in TOOLS.items()]

    async def call_tool(self, name, arguments):
        method = TOOLS[name][0]
        if method == "execute":
            result = await asyncio.to_thread(self.runtime.execute, arguments["preview_id"])
        else:
            result = await asyncio.to_thread(self.runtime.request, method, arguments)
            result = public_purchase(result) if method == "purchase" else public_result(name, result)
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))], structuredContent=result)


async def serve():
    with LocalRehearsal() as runtime:
        async def status(_):
            return await asyncio.to_thread(runtime.request, "snapshot")
        native = NativeTool(types.Tool(name="clink_node_status", description="Local Anvil budget and verified payment counters. TestUSD has no value.", inputSchema=object_schema({})), status)
        proxy = McpToolProxy({"marketplace":BudgetMarketplaceClient(runtime)}, native_tools={"clink_node_status":native})
        server = create_mcp_server(proxy)
        async with stdio_server() as (read, write):
            await server.run(read,write,server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(serve())
