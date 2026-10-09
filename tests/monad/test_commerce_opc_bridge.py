from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import anyio
import httpx
import pytest

from apps.node.clink_node.opc_client import OpcClientError, OpcClientStateStore


ORIGIN = "https://review.agentonomy.xyz"
PAIRING_TOKEN = "a" * 43


def _client(tmp_path: Path, handler, now: list[int], *, poll_interval_seconds: float = 2.0):
    from examples.monad_commerce.opc_bridge import CommerceOpcClient

    client = CommerceOpcClient(
        OpcClientStateStore(tmp_path / "opc.json"),
        origin=ORIGIN,
        label="Commerce test Agent",
        clock=lambda: now[0],
        transport=httpx.MockTransport(handler),
        poll_interval_seconds=poll_interval_seconds,
    )
    return client


def _status(installation_id: str, status: str, **extra: object) -> dict[str, object]:
    return {
        "installation_id": installation_id,
        "status": status,
        "label": "Commerce test Agent",
        "scope": "payments",
        **extra,
    }


def _bridge_value(result) -> dict[str, object]:
    assert isinstance(result.structuredContent, dict)
    return result.structuredContent


def test_commerce_bridge_refreshes_pending_pair_after_browser_logout_relogin(
    tmp_path: Path,
) -> None:
    """A second explicit login must not reopen a link bound to the old browser."""

    from examples.monad_commerce.opc_bridge import CommerceOpcOnboardingBridge

    now = [7_000]
    holder: dict[str, object] = {}
    pairings: list[str] = []
    opened: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        client = holder["client"]
        assert hasattr(client, "state")
        installation_id = client.state.installation_id
        if request.url.path == "/v1/opc/status":
            return httpx.Response(200, json=_status(installation_id, "pending"))
        if request.url.path == "/v1/opc/pairings":
            token = ("a" if not pairings else "b") * 43
            pairings.append(token)
            return httpx.Response(
                201,
                json={
                    "installation_id": installation_id,
                    "pairing_id": "opc_pair_" + str(len(pairings)).zfill(40),
                    "verification_uri": f"{ORIGIN}/account/{token}",
                    "expires_at": now[0] + 90,
                    "status": "pending",
                },
            )
        raise AssertionError(f"unexpected OPC path: {request.url.path}")

    client = _client(tmp_path, handler, now, poll_interval_seconds=60)
    holder["client"] = client
    bridge = CommerceOpcOnboardingBridge(
        client,
        browser_opener=lambda uri: opened.append(uri) or True,
    )
    try:
        first, second = anyio.run(
            lambda: _connect_twice(bridge),
        )
    finally:
        client.close()

    assert _bridge_value(first)["status"] == "pending"
    assert _bridge_value(second)["status"] == "pending"
    assert len(pairings) == 2
    assert opened == [f"{ORIGIN}/account/{token}" for token in pairings]
    assert opened[0] != opened[1]
    assert all(token not in json.dumps(_bridge_value(result)) for token in pairings for result in (first, second))


async def _connect_twice(bridge) -> tuple[object, object]:
    first = await bridge.call_tool("connect_clink_wallet", {})
    second = await bridge.call_tool("connect_clink_wallet", {})
    return first, second


def test_commerce_bridge_retries_after_fresh_pair_proof_failure(
    tmp_path: Path, capsys
) -> None:
    """A failed fresh broker proof must not poison the next explicit connect."""

    from examples.monad_commerce.opc_bridge import CommerceOpcOnboardingBridge

    now = [8_000]
    holder: dict[str, object] = {}
    pair_calls = 0
    opened: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal pair_calls
        client = holder["client"]
        assert hasattr(client, "state")
        installation_id = client.state.installation_id
        if request.url.path == "/v1/opc/status":
            return httpx.Response(200, json=_status(installation_id, "pending"))
        if request.url.path == "/v1/opc/pairings":
            pair_calls += 1
            if pair_calls == 1:
                return httpx.Response(403, json={"error": "stale proof"})
            return httpx.Response(
                201,
                json={
                    "installation_id": installation_id,
                    "pairing_id": "opc_pair_" + "c" * 40,
                    "verification_uri": f"{ORIGIN}/account/{'c' * 43}",
                    "expires_at": now[0] + 90,
                    "status": "pending",
                },
            )
        raise AssertionError(f"unexpected OPC path: {request.url.path}")

    client = _client(tmp_path, handler, now, poll_interval_seconds=60)
    holder["client"] = client
    bridge = CommerceOpcOnboardingBridge(
        client,
        browser_opener=lambda uri: opened.append(uri) or True,
    )
    try:
        first, second = anyio.run(lambda: _connect_twice(bridge))
    finally:
        client.close()

    first_value = _bridge_value(first)
    second_value = _bridge_value(second)
    assert first_value["status"] == "unavailable"
    assert second_value["status"] == "pending"
    assert second_value["browser_opened"] is True
    assert pair_calls == 2
    assert len(opened) == 1
    captured = capsys.readouterr()
    assert "stale proof" not in captured.out + captured.err


def test_commerce_bridge_rebinds_through_real_external_service_after_logout(
    tmp_path: Path,
) -> None:
    """The broker rejects A's link for B, while a fresh Commerce bridge link works."""

    from apps.node.clink_node.opc_onboarding import OpcOnboardingBridge
    from examples.monad_commerce.external_opc import ExternalHostedCommerceService
    from examples.monad_commerce.opc_bridge import (
        CommerceOpcClient,
        CommerceOpcOnboardingBridge,
    )
    from test_external_opc import (
        DeviceCanary,
        NOW as SERVICE_NOW,
        ORIGIN as SERVICE_ORIGIN,
        OWNER_A as WALLET_OWNER,
        configuration,
        login,
    )

    old_now = SERVICE_NOW[0]
    DeviceCanary.devices = {}
    DeviceCanary.tokens = {}
    SERVICE_NOW[0] = 2_000_000_000

    def make_handler(service):
        def handler(request: httpx.Request) -> httpx.Response:
            action = request.url.path.rsplit("/", 1)[-1]
            action = "pair" if action == "pairings" else action
            if action not in {"pair", "status", "token", "revoke"}:
                raise AssertionError(f"unexpected OPC path: {request.url.path}")
            body = json.loads(request.content.decode("utf-8"))
            try:
                value = service.device_request(action, body["proof"])
            except PermissionError:
                return httpx.Response(401, json={"error": "device_authorization_required"})
            except ValueError:
                return httpx.Response(400, json={"error": "invalid_device_proof"})
            return httpx.Response(201 if action == "pair" else 200, json=value)

        return handler

    service_dir = tmp_path / "broker"
    client_state = tmp_path / "opc.json"
    with ExternalHostedCommerceService(
        service_dir,
        configuration(),
        public_origin=SERVICE_ORIGIN,
        canary_factory=DeviceCanary,
        clock=lambda: SERVICE_NOW[0],
    ) as service:
        handler = make_handler(service)
        generic_opened: list[str] = []
        client = CommerceOpcClient(
            OpcClientStateStore(client_state),
            origin=SERVICE_ORIGIN,
            label="External integration Agent",
            clock=lambda: SERVICE_NOW[0],
            transport=httpx.MockTransport(handler),
            poll_interval_seconds=60,
        )
        try:
            generic = OpcOnboardingBridge(
                client,
                browser_opener=lambda uri: generic_opened.append(uri) or True,
            )
            first = anyio.run(lambda: generic.call_tool("connect_clink_wallet", {}))
            assert _bridge_value(first)["status"] == "pending"
            old_request = generic_opened[0].rsplit("/", 1)[1]

            browser_a, csrf_a = login(service, WALLET_OWNER)
            assert service.claim_device(browser_a, csrf_a, old_request)["status"] == "claimed"
            service.logout(browser_a, csrf_a)
            browser_b, csrf_b = login(service, WALLET_OWNER)

            # The shared bridge refreshes device status, but still reopens its
            # cached A-bound URI. The real broker must reject it for B.
            stale = anyio.run(lambda: generic.call_tool("connect_clink_wallet", {}))
            assert _bridge_value(stale)["status"] == "pending"
            assert generic_opened[-1] == generic_opened[0]
            with pytest.raises(PermissionError):
                service.claim_device(browser_b, csrf_b, old_request)
        finally:
            client.close()

        commerce_opened: list[str] = []
        commerce_client = CommerceOpcClient(
            OpcClientStateStore(client_state),
            origin=SERVICE_ORIGIN,
            label="External integration Agent",
            clock=lambda: SERVICE_NOW[0],
            transport=httpx.MockTransport(handler),
            poll_interval_seconds=60,
        )
        try:
            commerce = CommerceOpcOnboardingBridge(
                commerce_client,
                browser_opener=lambda uri: commerce_opened.append(uri) or True,
            )
            fresh = anyio.run(lambda: commerce.call_tool("connect_clink_wallet", {}))
            assert _bridge_value(fresh)["status"] == "pending"
            fresh_request = commerce_opened[-1].rsplit("/", 1)[1]
            assert fresh_request != old_request

            # B can claim the new unbound link. The next fresh device proof is
            # routed through the broker to the tenant's consent adapter.
            assert service.claim_device(browser_b, csrf_b, fresh_request)["status"] == "claimed"
            challenged = anyio.run(
                lambda: commerce.call_tool("connect_clink_wallet", {})
            )
            assert _bridge_value(challenged)["status"] == "pending"
            challenge_request = commerce_opened[-1].rsplit("/", 1)[1]
            binding = service.claim_device(browser_b, csrf_b, challenge_request)
            assert binding["status"] == "challenge"
            assert binding["challenge"]["session_id"] == "consent_challenge"
            assert old_request not in json.dumps(
                [_bridge_value(fresh), _bridge_value(challenged)]
            )
        finally:
            commerce_client.close()
    DeviceCanary.devices = {}
    DeviceCanary.tokens = {}
    SERVICE_NOW[0] = old_now


def test_expired_routing_intent_gets_one_fresh_pair_and_never_loops(tmp_path: Path) -> None:
    now = [1_000]
    calls: list[tuple[str, dict[str, object]]] = []
    holder: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        calls.append((request.url.path, body))
        client = holder["client"]
        assert hasattr(client, "state")
        installation_id = client.state.installation_id
        if request.url.path == "/v1/opc/pairings":
            pair_number = sum(path == request.url.path for path, _ in calls)
            return httpx.Response(
                201,
                json={
                    "installation_id": installation_id,
                    "pairing_id": f"opc_pair_{pair_number:040x}",
                    "verification_uri": f"{ORIGIN}/account/{PAIRING_TOKEN}",
                    "expires_at": now[0] + 90,
                    "status": "pending",
                },
            )
        if request.url.path == "/v1/opc/status":
            status_number = sum(path == request.url.path for path, _ in calls)
            payload = _status(installation_id, "pending")
            if status_number == 1 or status_number >= 3:
                payload["pairing_required"] = True
            return httpx.Response(
                200,
                json=payload,
            )
        raise AssertionError(f"unexpected OPC path: {request.url.path}")

    client = _client(tmp_path, handler, now, poll_interval_seconds=60)
    holder["client"] = client
    try:
        first_pair = client.connect()
        assert set(first_pair) == {
            "installation_id",
            "pairing_id",
            "verification_uri",
            "expires_at",
            "status",
        }
        assert first_pair["verification_uri"].startswith(f"{ORIGIN}/account/")

        now[0] += 91
        refreshed = client.status()
        assert refreshed["status"] == "pending"
        pair_calls = [path for path, _ in calls if path == "/v1/opc/pairings"]
        assert len(pair_calls) == 2
        assert calls[0][1]["proof"] != calls[2][1]["proof"]

        client.status()
        pair_calls = [path for path, _ in calls if path == "/v1/opc/pairings"]
        assert len(pair_calls) == 2
    finally:
        client.close()


def test_pairing_required_is_only_valid_for_pending_status(tmp_path: Path) -> None:
    now = [2_000]
    holder: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        client = holder["client"]
        if request.url.path != "/v1/opc/status":
            raise AssertionError(f"unexpected OPC path: {request.url.path}")
        return httpx.Response(
            200,
            json=_status(client.state.installation_id, "active", pairing_required=True),
        )

    client = _client(tmp_path, handler, now, poll_interval_seconds=60)
    holder["client"] = client
    try:
        with pytest.raises(OpcClientError, match="status response is invalid"):
            client.status()
    finally:
        client.close()


def test_no_token_is_minted_or_printed_before_consent(tmp_path: Path, capsys) -> None:
    now = [3_000]
    holder: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        client = holder["client"]
        if request.url.path != "/v1/opc/token":
            raise AssertionError(f"unexpected OPC path: {request.url.path}")
        return httpx.Response(403, json={"error": "secret-token-must-not-leak"})

    client = _client(tmp_path, handler, now, poll_interval_seconds=60)
    holder["client"] = client
    try:
        with pytest.raises(OpcClientError, match="HTTP 403"):
            client.access_token()
        captured = capsys.readouterr()
        assert "secret-token-must-not-leak" not in captured.out + captured.err
        assert client._access_token is None
    finally:
        client.close()


def test_background_poll_stops_on_active_and_explicit_stop(tmp_path: Path) -> None:
    now = [4_000]
    active_seen = threading.Event()
    holder: dict[str, object] = {}
    status_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_calls
        client = holder["client"]
        if request.url.path == "/v1/opc/pairings":
            return httpx.Response(
                201,
                json={
                    "installation_id": client.state.installation_id,
                    "pairing_id": "opc_pair_" + "1" * 40,
                    "verification_uri": f"{ORIGIN}/account/{PAIRING_TOKEN}",
                    "expires_at": now[0] + 90,
                    "status": "pending",
                },
            )
        if request.url.path == "/v1/opc/status":
            status_calls += 1
            active_seen.set()
            return httpx.Response(200, json=_status(client.state.installation_id, "active"))
        raise AssertionError(f"unexpected OPC path: {request.url.path}")

    client = _client(tmp_path, handler, now, poll_interval_seconds=0.01)
    holder["client"] = client
    try:
        client.connect()
        assert active_seen.wait(1)
        deadline = time.monotonic() + 1
        while client.is_polling() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert status_calls >= 1
        assert client.is_polling() is False

        client.connect()
        deadline = time.monotonic() + 1
        while not client.is_polling() and time.monotonic() < deadline:
            time.sleep(0.01)
        client.stop_polling()
        assert client.is_polling() is False
    finally:
        client.close()


def test_http_and_device_calls_are_serialized(tmp_path: Path) -> None:
    now = [5_000]
    holder: dict[str, object] = {}
    active = 0
    maximum = 0
    guard = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum
        client = holder["client"]
        if request.url.path != "/v1/opc/status":
            raise AssertionError(f"unexpected OPC path: {request.url.path}")
        with guard:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(0.02)
            return httpx.Response(200, json=_status(client.state.installation_id, "pending"))
        finally:
            with guard:
                active -= 1

    client = _client(tmp_path, handler, now, poll_interval_seconds=60)
    holder["client"] = client
    try:
        threads = [threading.Thread(target=client.status) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert maximum == 1
    finally:
        client.close()


@pytest.mark.parametrize(
    "verification_uri",
    [
        f"{ORIGIN}/account/",
        f"{ORIGIN}/account/opaque/extra",
        f"{ORIGIN}/account/opaque?token=leak",
        f"{ORIGIN}/account/opaque#fragment",
        f"{ORIGIN}/account/{'a' * 42}",
        f"{ORIGIN}/account/{'a' * 44}",
        f"{ORIGIN}/account/{'a' * 40}%2F",
        f"{ORIGIN}/account/{'a' * 42}+",
        "https://evil.invalid/account/opaque",
        f"{ORIGIN}/other/opaque",
    ],
)
def test_inherited_pairing_link_safety_remains_strict(
    tmp_path: Path, verification_uri: str
) -> None:
    now = [6_000]
    holder: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        client = holder["client"]
        if request.url.path != "/v1/opc/pairings":
            raise AssertionError(f"unexpected OPC path: {request.url.path}")
        return httpx.Response(
            201,
            json={
                "installation_id": client.state.installation_id,
                "pairing_id": "opc_pair_" + "2" * 40,
                "verification_uri": verification_uri,
                "expires_at": now[0] + 90,
                "status": "pending",
            },
        )

    client = _client(tmp_path, handler, now, poll_interval_seconds=60)
    holder["client"] = client
    try:
        with pytest.raises(OpcClientError, match="pairing response is invalid"):
            client.connect()
    finally:
        client.close()


def test_cli_uses_existing_stdio_bridge_without_emitting_tokens(tmp_path: Path, monkeypatch, capsys) -> None:
    import examples.monad_commerce.opc_bridge as bridge

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, store, *, origin, label):
            captured.update(store=store, origin=origin, label=label)

        async def serve_stdio(self):
            return None

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(bridge, "CommerceOpcClient", FakeClient)
    assert bridge.main(
        ["--origin", ORIGIN, "--state", str(tmp_path / "owner-only.json"), "--label", "Demo Agent"]
    ) == 0
    assert captured["origin"] == ORIGIN
    assert captured["label"] == "Demo Agent"
    assert captured["closed"] is True
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""


def test_client_rejects_existing_state_bound_to_another_origin(tmp_path: Path) -> None:
    from examples.monad_commerce.opc_bridge import CommerceOpcClient

    path = tmp_path / "owner-only.json"
    first = CommerceOpcClient(
        OpcClientStateStore(path),
        origin=ORIGIN,
        label="Demo Agent",
        poll_interval_seconds=60,
    )
    first.close()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="already bound"):
        CommerceOpcClient(
            OpcClientStateStore(path),
            origin="https://other.invalid",
            label="Different Agent",
            poll_interval_seconds=60,
        )
    assert path.read_bytes() == before


def test_cli_starts_from_repo_root_without_pythonpath_and_exits_on_stdin_eof(tmp_path: Path) -> None:
    state_path = tmp_path / "owner-only.json"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.monad_commerce.opc_bridge",
            "--origin",
            ORIGIN,
            "--state",
            str(state_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        input=b"",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    assert completed.stdout == b""
    assert completed.stderr == b""
    assert state_path.exists()
