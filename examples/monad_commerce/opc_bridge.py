"""External Agent OPC bridge for the hosted commerce example.

The hosted page is the consent surface.  This module keeps the Agent-side
device proof and the canonical OPC MCP client in one process, while allowing a
pending browser pairing to complete without another Agent turn.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
from threading import Event, RLock, Thread, current_thread
from typing import Any, Callable
from urllib.parse import urlsplit

import anyio

# Reuse the standalone composition's Core/Node namespace bootstrap.
from examples.commerce import node as _node_bootstrap
from apps.node.clink_node.opc_client import (
    OpcClient,
    OpcClientError,
    OpcClientStateStore,
)
from apps.node.clink_node.opc_onboarding import OpcOnboardingBridge


_STATUS_VALUES = frozenset(
    {"pending", "active", "consent_required", "revoked", "unpaired"}
)
_STATUS_FIELDS = frozenset(
    {
        "installation_id",
        "status",
        "label",
        "scope",
        "consent_expires_at",
        "created_at",
        "updated_at",
        "pairing_required",
    }
)
_DEFAULT_POLL_INTERVAL_SECONDS = 2.0
_PAIRING_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class CommerceOpcClient(OpcClient):
    """An OPC client with bounded pairing refresh and background status poll."""

    def __init__(
        self,
        store: OpcClientStateStore,
        *,
        origin: str | None = None,
        label: str | None = None,
        allow_loopback_http: bool = False,
        clock: Callable[[], int] | None = None,
        transport: Any | None = None,
        mcp_client_factory: Callable[..., Any] | None = None,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._poll_interval_seconds = self._validate_poll_interval(
            poll_interval_seconds
        )
        super().__init__(
            store,
            origin=origin,
            label=label,
            allow_loopback_http=allow_loopback_http,
            clock=clock,
            transport=transport,
            mcp_client_factory=mcp_client_factory,
        )
        self._commerce_lock = RLock()
        self._poll_stop_event: Event | None = None
        self._poll_thread: Thread | None = None
        self._pairing_refresh_used = False
        self._background_error: str | None = None

    @classmethod
    def from_state(
        cls,
        state,
        *,
        clock: Callable[[], int] | None = None,
        transport: Any | None = None,
        mcp_client_factory: Callable[..., Any] | None = None,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> "CommerceOpcClient":
        """Build a client from an already-loaded state without persisting it."""

        instance = super().from_state(
            state,
            clock=clock,
            transport=transport,
            mcp_client_factory=mcp_client_factory,
        )
        instance._poll_interval_seconds = instance._validate_poll_interval(
            poll_interval_seconds
        )
        instance._commerce_lock = RLock()
        instance._poll_stop_event = None
        instance._poll_thread = None
        instance._pairing_refresh_used = False
        instance._background_error = None
        return instance

    @staticmethod
    def _validate_poll_interval(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("OPC poll interval must be a positive finite number")
        interval = float(value)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("OPC poll interval must be a positive finite number")
        return interval

    def connect(self) -> dict[str, Any]:
        """Create one canonical routing intent and start bounded polling."""

        with self._commerce_lock:
            self._pairing_refresh_used = False
            pairing = super().connect()
            self._validate_pairing_link(pairing)
            self._start_polling_locked(pairing)
            return pairing

    def status(self) -> dict[str, Any]:
        """Read status and perform at most one fresh pair after consent expiry."""

        with self._commerce_lock:
            result = self._status_once_locked()
            if (
                result.get("status") == "pending"
                and result.get("pairing_required") is True
                and not self._pairing_refresh_used
            ):
                # Set the guard before the new proof so any error or re-entrant
                # call cannot turn a stale routing intent into a retry loop.
                self._pairing_refresh_used = True
                pairing = super().connect()
                self._validate_pairing_link(pairing)
                self._start_polling_locked(pairing)
                result = self._status_once_locked()

            if result.get("status") in {"active", "revoked"}:
                self._signal_stop_polling_locked()
            return result

    def _status_once_locked(self) -> dict[str, Any]:
        result, request_id = super()._proof_request("status", expected_status=200)
        if not self._valid_status_response(result):
            raise OpcClientError("OPC status response is invalid")
        self._complete_request("status", request_id)
        return result

    def _valid_status_response(self, result: dict[str, Any]) -> bool:
        if (
            not set(result).issubset(_STATUS_FIELDS)
            or not {"installation_id", "status"}.issubset(result)
            or result.get("installation_id") != self.state.installation_id
            or result.get("status") not in _STATUS_VALUES
        ):
            return False
        if "pairing_required" in result:
            if type(result["pairing_required"]) is not bool:
                return False
            if result["pairing_required"] is not True or result.get("status") != "pending":
                return False
        if "label" in result and (
            type(result["label"]) is not str or not 1 <= len(result["label"]) <= 80
        ):
            return False
        if "scope" in result and result["scope"] != "payments":
            return False
        return not any(
            name in result
            and result[name] is not None
            and type(result[name]) is not str
            for name in ("consent_expires_at", "created_at", "updated_at")
        )

    def access_token(self) -> str:
        with self._commerce_lock:
            return super().access_token()

    def revoke(self) -> dict[str, Any]:
        with self._commerce_lock:
            result = super().revoke()
            self._signal_stop_polling_locked()
            return result

    def open_account_link(self) -> dict[str, Any]:
        with self._commerce_lock:
            return super().open_account_link()

    def _remote_mcp_client(self):
        with self._commerce_lock:
            return super()._remote_mcp_client()

    def _proof_request(self, *args: Any, **kwargs: Any):
        # The base implementation signs device proofs and mutates its request
        # journal.  Serialize those operations with all status/token actions.
        with self._commerce_lock:
            return super()._proof_request(*args, **kwargs)

    def stop_polling(self) -> None:
        with self._commerce_lock:
            thread = self._poll_thread
            event = self._poll_stop_event
            if event is not None:
                event.set()
            self._poll_thread = None
            self._poll_stop_event = None
        if thread is not None and thread is not current_thread():
            thread.join(timeout=max(1.0, self._poll_interval_seconds * 2))

    def is_polling(self) -> bool:
        with self._commerce_lock:
            return bool(
                self._poll_thread is not None
                and self._poll_thread.is_alive()
                and self._poll_stop_event is not None
                and not self._poll_stop_event.is_set()
            )

    def close(self) -> None:
        self.stop_polling()
        with self._commerce_lock:
            super().close()

    async def serve_stdio(self) -> None:
        """Serve the copied onboarding state machine using the pinned MCP SDK."""

        from mcp.server import stdio
        from mcp.server.lowlevel import Server

        server = Server("Agentonomy OPC bridge", version="0.1.0")
        bridge = CommerceOpcOnboardingBridge(self)

        @server.list_tools()
        async def list_tools():
            return await bridge.list_tools()

        @server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict[str, Any]):
            return await bridge.call_tool(name, arguments)

        async with stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
                raise_exceptions=True,
            )

    def _start_polling_locked(self, pairing: dict[str, Any]) -> None:
        if pairing.get("status") != "pending":
            self._signal_stop_polling_locked()
            return
        expires_at = pairing.get("expires_at")
        if type(expires_at) is not int or expires_at <= self._clock():
            self._signal_stop_polling_locked()
            return

        self._signal_stop_polling_locked()
        event = Event()
        thread = Thread(
            target=self._poll_loop,
            args=(event, expires_at),
            name="commerce-opc-status-poll",
            daemon=True,
        )
        self._poll_stop_event = event
        self._poll_thread = thread
        thread.start()

    def _validate_pairing_link(self, pairing: dict[str, Any]) -> None:
        """Require a canonical account link on this same OPC origin."""

        verification_uri = pairing.get("verification_uri")
        try:
            parsed = urlsplit(verification_uri) if isinstance(verification_uri, str) else None
        except ValueError:
            parsed = None
        if parsed is None:
            raise OpcClientError("OPC pairing response is invalid")
        verification_origin = f"{parsed.scheme}://{parsed.netloc}"
        if (
            verification_origin != self.state.origin
            or not parsed.path.startswith("/account/")
            or not _PAIRING_TOKEN_RE.fullmatch(parsed.path[len("/account/"):])
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise OpcClientError("OPC pairing response is invalid")

    def _signal_stop_polling_locked(self) -> None:
        if self._poll_stop_event is not None:
            self._poll_stop_event.set()
        self._poll_stop_event = None
        self._poll_thread = None

    def _poll_loop(self, event: Event, expires_at: int) -> None:
        try:
            while not event.is_set():
                remaining = expires_at - self._clock()
                if remaining <= 0:
                    break
                if event.wait(min(self._poll_interval_seconds, remaining)):
                    break
                try:
                    result = self.status()
                except Exception:
                    # Polling must never print a remote response, proof, token,
                    # or traceback.  The one local marker is intentionally
                    # generic and does not change commerce state.
                    with self._commerce_lock:
                        if self._poll_stop_event is event:
                            self._background_error = "OPC status polling stopped"
                    break
                if result.get("status") in {"active", "revoked"}:
                    break
        finally:
            with self._commerce_lock:
                if self._poll_stop_event is event:
                    self._poll_stop_event = None
                    self._poll_thread = None


class CommerceOpcOnboardingBridge(OpcOnboardingBridge):
    """Keep the shared onboarding lifecycle without reusing browser pairings.

    The hosted broker binds a pending verification link to the browser session
    that opened it.  A later explicit connect can therefore represent a new
    browser login even while the device remains pending.  The shared bridge
    still owns status refresh, single-flight, active management, and revoked
    handling; this override only disables its pending-link cache.
    """

    def _cached_pairing(self) -> dict[str, Any] | None:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the hosted commerce OPC bridge")
    parser.add_argument("--origin", required=True, help="Hosted OPC origin")
    parser.add_argument(
        "--state",
        required=True,
        type=Path,
        help="Owner-only OPC client state path",
    )
    parser.add_argument("--label", default="Commerce Agent", help="Installation label")
    args = parser.parse_args(argv)

    client = CommerceOpcClient(
        OpcClientStateStore(args.state.expanduser()),
        origin=args.origin,
        label=args.label,
    )
    try:
        anyio.run(client.serve_stdio)
    finally:
        client.close()
    return 0


__all__ = ["CommerceOpcClient", "CommerceOpcOnboardingBridge", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
