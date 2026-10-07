"""Authenticated, in-process MCP calls for the hosted website Agent.

The hosted website does not open a network connection to its own MCP surface.
This module still exercises the canonical Node MCP gateway and SDK transport so
that authentication, tool discovery, schema validation and principal routing
remain the same as the public endpoint.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import re
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from apps.node.clink_node.mcp_gateway import create_mcp_application
from apps.node.clink_node.mcp_proxy import McpToolProxy


_OWNER = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TENANT = re.compile(r"^tenant_[0-9a-f]{32}$")
_MAX_TOKEN_LENGTH = 2048


@dataclass(frozen=True)
class HostedMcpPrincipal:
    """The Core-authenticated routing identity for one hosted MCP call."""

    tenant_id: str
    owner: str
    proxy: McpToolProxy = field(repr=False)


def _expected_tenant(owner: str) -> str:
    return "tenant_" + hashlib.sha256(owner.lower().encode("ascii")).hexdigest()[:32]


def _require_principal(principal: Any) -> HostedMcpPrincipal:
    if not isinstance(principal, HostedMcpPrincipal):
        raise PermissionError("hosted MCP principal required")
    if (
        type(principal.tenant_id) is not str
        or not _TENANT.fullmatch(principal.tenant_id)
        or type(principal.owner) is not str
        or not _OWNER.fullmatch(principal.owner)
        or principal.tenant_id != _expected_tenant(principal.owner)
        or not isinstance(principal.proxy, McpToolProxy)
    ):
        raise PermissionError("hosted MCP principal invalid")
    return principal


def _strict_tool(tool: types.Tool) -> types.Tool:
    schema = deepcopy(tool.inputSchema)
    if type(schema) is not dict or schema.get("type") != "object":
        raise RuntimeError("hosted MCP tool schema invalid")
    schema["additionalProperties"] = False
    return tool.model_copy(update={"inputSchema": schema})


class HostedMcpProxy:
    """Principal-aware adapter for the canonical Node MCP proxy."""

    async def list_tools(
        self,
        *,
        principal: HostedMcpPrincipal | None = None,
    ) -> list[types.Tool]:
        identity = _require_principal(principal)
        try:
            tools = await identity.proxy.list_tools()
            return [_strict_tool(tool) for tool in tools]
        except Exception:
            raise RuntimeError("hosted MCP tool listing failed") from None

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        principal: HostedMcpPrincipal | None = None,
    ) -> dict[str, Any] | types.CallToolResult:
        identity = _require_principal(principal)
        if type(name) is not str or not name:
            raise ValueError("hosted MCP tool name invalid")
        if type(arguments) is not dict:
            raise ValueError("hosted MCP arguments invalid")
        try:
            return await identity.proxy.call_tool(name, arguments)
        except Exception:
            raise RuntimeError("hosted MCP tool call failed") from None


def _validate_origin(origin: str) -> str:
    from shared.opc_protocol import canonical_opc_origin
    if type(origin) is not str:
        raise ValueError("hosted MCP origin invalid")
    try:
        canonical = canonical_opc_origin(origin)
    except ValueError:
        raise ValueError("hosted MCP origin invalid") from None
    if canonical != origin:
        raise ValueError("hosted MCP origin invalid")
    return canonical


def _validate_access_token(access_token: str) -> str:
    if (
        type(access_token) is not str
        or not access_token
        or len(access_token) > _MAX_TOKEN_LENGTH
        or any(ord(character) <= 32 or ord(character) >= 127 for character in access_token)
    ):
        raise ValueError("hosted MCP access token invalid")
    return access_token


def _http_status(error: BaseException) -> int | None:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            status = _http_status(nested)
            if status is not None:
                return status
    return None


async def call_hosted_tool(
    *,
    origin: str,
    access_service: Any,
    access_token: str,
    name: str,
    arguments: dict[str, Any],
) -> types.CallToolResult:
    """Call one hosted MCP tool through the canonical in-process HTTP stack."""

    if access_service is None:
        raise ValueError("hosted MCP access service required")
    base_origin = _validate_origin(origin)
    token = _validate_access_token(access_token)
    application = create_mcp_application(
        HostedMcpProxy(),
        access_service=access_service,
    )
    response_status: int | None = None

    async def record_response(response: httpx.Response) -> None:
        nonlocal response_status
        response_status = response.status_code

    try:
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application),
                base_url=base_origin,
                event_hooks={"response": [record_response]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json, text/event-stream",
                },
            ) as client:
                try:
                    async with streamable_http_client(
                        f"{base_origin}/mcp",
                        http_client=client,
                    ) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            result = await session.call_tool(name, arguments)
                except httpx.HTTPStatusError as error:
                    if error.response.status_code == 401:
                        raise PermissionError("hosted MCP unauthorized") from None
                    raise RuntimeError("hosted MCP transport rejected") from None
                except httpx.HTTPError:
                    raise RuntimeError("hosted MCP transport failed") from None
                except PermissionError:
                    raise
                except Exception as error:
                    if response_status == 401 or _http_status(error) == 401:
                        raise PermissionError("hosted MCP unauthorized") from None
                    raise RuntimeError("hosted MCP request failed") from None
    except PermissionError:
        raise
    except ValueError:
        raise
    except RuntimeError:
        raise
    except Exception as error:
        if response_status == 401 or _http_status(error) == 401:
            raise PermissionError("hosted MCP unauthorized") from None
        raise RuntimeError("hosted MCP request failed") from None
    if not isinstance(result, types.CallToolResult):
        raise RuntimeError("hosted MCP result invalid")
    return result


__all__ = [
    "HostedMcpPrincipal",
    "HostedMcpProxy",
    "call_hosted_tool",
]
