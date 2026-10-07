from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from apps.node.clink_node.mcp_gateway import create_mcp_application
from apps.node.clink_node.mcp_proxy import McpToolProxy
from examples.monad_commerce.hosted_mcp import (
    HostedMcpPrincipal,
    HostedMcpProxy,
    call_hosted_tool,
)


ORIGIN = "https://hosted.test"
OWNER_A = "0x" + "a" * 40
OWNER_B = "0x" + "b" * 40


@pytest.mark.parametrize("origin", [
    "file:///tmp/private", "http://hosted.test", "https://token@hosted.test",
    "https://hosted.test?secret=private", "https://hosted.test/#private",
    "https://hosted.test/", "https://hosted.test/path",
])
def test_hosted_transport_rejects_noncanonical_origins_before_auth(origin):
    class NoAdmission:
        def authenticate(self, _):
            raise AssertionError("must not attempt authentication")

    with pytest.raises(ValueError, match="origin"):
        asyncio.run(call_hosted_tool(origin=origin, access_service=NoAdmission(),
                                     access_token="opaque", name="who_am_i", arguments={}))


def tenant_id(owner: str) -> str:
    return "tenant_" + hashlib.sha256(owner.lower().encode()).hexdigest()[:32]


class OwnedProxy(McpToolProxy):
    def __init__(self, owner: str) -> None:
        super().__init__({})
        self.owner = owner
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self) -> list[types.Tool]:
        return [
            types.Tool(
                name="who_am_i",
                description="Return the authenticated wallet.",
                inputSchema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                },
            )
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict,
    ) -> types.CallToolResult:
        self.calls.append((name, arguments))
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=self.owner,
                )
            ],
            structuredContent={"owner": self.owner, "value": arguments.get("value")},
        )


class AccessService:
    def __init__(self) -> None:
        self.principals = {
            "token-a": HostedMcpPrincipal(
                tenant_id=tenant_id(OWNER_A),
                owner=OWNER_A,
                proxy=OwnedProxy(OWNER_A),
            ),
            "token-b": HostedMcpPrincipal(
                tenant_id=tenant_id(OWNER_B),
                owner=OWNER_B,
                proxy=OwnedProxy(OWNER_B),
            ),
        }
        self.tokens: list[str] = []

    def authenticate(self, token: str) -> HostedMcpPrincipal:
        self.tokens.append(token)
        try:
            return self.principals[token]
        except KeyError:
            raise ValueError("unauthorized") from None


async def _sdk_session(app, token: str):
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=ORIGIN,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
    )
    stack = client
    await stack.__aenter__()
    stream = streamable_http_client(f"{ORIGIN}/mcp", http_client=client)
    read, write, _ = await stream.__aenter__()
    session = ClientSession(read, write)
    await session.__aenter__()
    await session.initialize()
    return client, stream, session


async def _close_sdk_session(client, stream, session) -> None:
    await session.__aexit__(None, None, None)
    await stream.__aexit__(None, None, None)
    await client.__aexit__(None, None, None)


def test_hosted_mcp_uses_real_sdk_tools_list_init_and_call_with_strict_inputs() -> None:
    async def scenario() -> None:
        access = AccessService()
        app = create_mcp_application(HostedMcpProxy(), access_service=access)
        async with app.router.lifespan_context(app):
            client, stream, session = await _sdk_session(app, "token-a")
            try:
                listed = await session.list_tools()
                tool = next(item for item in listed.tools if item.name == "who_am_i")
                assert tool.inputSchema["additionalProperties"] is False

                result = await session.call_tool("who_am_i", {"value": "ok"})
                assert result.isError is False
                assert result.structuredContent == {"owner": OWNER_A, "value": "ok"}

                rejected = await session.call_tool(
                    "who_am_i",
                    {"value": "ok", "user_id": OWNER_B},
                )
                assert rejected.isError is True
                assert access.principals["token-a"].proxy.calls == [
                    ("who_am_i", {"value": "ok"})
                ]
            finally:
                await _close_sdk_session(client, stream, session)

    asyncio.run(scenario())

def test_call_hosted_tool_routes_each_principal_without_ambient_identity() -> None:
    async def scenario() -> None:
        access = AccessService()
        results = await asyncio.gather(
            call_hosted_tool(
                origin=ORIGIN,
                access_service=access,
                access_token="token-a",
                name="who_am_i",
                arguments={"value": "a"},
            ),
            call_hosted_tool(
                origin=ORIGIN,
                access_service=access,
                access_token="token-b",
                name="who_am_i",
                arguments={"value": "b"},
            ),
        )
        assert all(isinstance(result, types.CallToolResult) for result in results)
        assert [result.structuredContent for result in results] == [
            {"owner": OWNER_A, "value": "a"},
            {"owner": OWNER_B, "value": "b"},
        ]
        assert access.principals["token-a"].proxy.calls == [
            ("who_am_i", {"value": "a"})
        ]
        assert access.principals["token-b"].proxy.calls == [
            ("who_am_i", {"value": "b"})
        ]

    asyncio.run(scenario())


def test_hosted_mcp_rejects_unauthorized_and_invalid_principals_without_token_leaks() -> None:
    async def scenario() -> None:
        access = AccessService()
        secret = "token-that-must-not-escape"
        with pytest.raises(PermissionError) as caught:
            await call_hosted_tool(
                origin=ORIGIN,
                access_service=access,
                access_token=secret,
                name="who_am_i",
                arguments={},
            )
        assert secret not in str(caught.value)

        adapter = HostedMcpProxy()
        with pytest.raises(PermissionError):
            await adapter.list_tools(principal=None)
        with pytest.raises(PermissionError):
            await adapter.call_tool("who_am_i", {}, principal=None)

    asyncio.run(scenario())
