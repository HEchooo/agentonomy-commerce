from __future__ import annotations

import asyncio
from pathlib import Path
import threading

from mcp import types
import pytest

from examples.monad_commerce.hosted_service import HostedCommerceService


ORIGIN = "https://commerce.example"
OWNER_A = "0x" + "aa" * 20
OWNER_B = "0x" + "bb" * 20


def _configuration() -> dict[str, object]:
    network = {
        "mode": "monad_testnet",
        "chain_id": 10143,
        "rpc_urls": ["https://rpc.example.org", "https://rpc.example.net"],
        "token": "0x" + "11" * 20,
        "executor": "0x" + "22" * 20,
        "payee": "0x" + "33" * 20,
    }
    signer = {
        "profile": "hackathon",
        "region": "ap-southeast-1",
        "account_id": "123456789012",
        "expected_role_arn": "arn:aws:iam::123456789012:role/test-runtime",
        "credential_directory": "/tmp/isolated-hosted-lifecycle-test",
        "execution_key_arn": (
            "arn:aws:kms:ap-southeast-1:123456789012:key/"
            "11111111-1111-4111-8111-111111111111"
        ),
        "gas_key_arn": (
            "arn:aws:kms:ap-southeast-1:123456789012:key/"
            "22222222-2222-4222-8222-222222222222"
        ),
        "scope": {
            "network": network,
            "owner": OWNER_A,
            "execution_address": "0x" + "44" * 20,
            "relayer_address": "0x" + "55" * 20,
            "nonce_min": 3,
            "nonce_max": 5,
        },
    }
    return {
        "deployment": network
        | {
            "owner": OWNER_A,
            "execution_signer": "0x" + "44" * 20,
            "relayer": "0x" + "55" * 20,
        },
        "signer_configuration": signer,
    }


class LifecycleCanary:
    created: list["LifecycleCanary"] = []

    def __init__(self, path: Path, config: dict[str, object], **kwargs: object):
        self.path = path
        self.config = config
        self.owner = config["deployment"]["owner"]  # type: ignore[index]
        self.tenant_id = kwargs["tenant_id"]
        self.token = "agent-" + self.owner[-8:]
        self.authorized: set[str] = set()
        self.closed = False
        type(self).created.append(self)

    def __enter__(self) -> "LifecycleCanary":
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def hosted(self, method: str, **params: object) -> dict[str, object] | None:
        if self.closed:
            raise RuntimeError("canary is closed")
        digest = params.get("browser_digest")
        if method == "browser_resume":
            return None
        if method == "browser_challenge":
            return {"session_id": "challenge-" + self.owner[-8:], "message_to_sign": self.owner}
        if method == "browser_verify":
            if params.get("signature") != self.owner:
                raise ValueError("wallet proof rejected")
            self.authorized.add(digest)  # type: ignore[arg-type]
            return {"wallet_address": self.owner}
        if method == "browser_authorize":
            if digest not in self.authorized:  # type: ignore[operator]
                raise ValueError("wallet authorization required")
            return {"wallet_address": self.owner}
        if method == "browser_logout":
            self.authorized.discard(digest)  # type: ignore[arg-type]
            return {"status": "logged_out"}
        if method == "opc_token":
            return {"access_token": self.token}
        if method == "opc_authenticate":
            if params.get("access_token") != self.token:
                raise ValueError("OPC token rejected")
            suffix = self.owner[-8:]
            return {
                "issuer": "opc",
                "user_id": "commerce-demo-user",
                "agent_id": "hermes",
                "scope": "payments",
                "wallet_identity_id": "identity-" + suffix,
                "spending_grant_id": "grant-" + suffix,
                "installation_id": "installation-" + suffix,
                "credential_id": "credential-" + suffix,
                "expires_at": 4102444800,
            }
        raise AssertionError(method)


@pytest.fixture
def service(tmp_path: Path):
    LifecycleCanary.created = []
    hosted = HostedCommerceService(
        tmp_path,
        _configuration(),
        public_origin=ORIGIN,
        canary_factory=LifecycleCanary,
    )
    hosted.__enter__()
    hosted._proof = lambda _row, _action: "proof"
    try:
        yield hosted
    finally:
        if hosted.db is not None:
            hosted.__exit__(None, None, None)


def _login(service: HostedCommerceService, prefix: str, owner: str) -> tuple[str, str]:
    browser = (prefix + "b" * 43)[:43]
    csrf = (prefix + "c" * 43)[:43]
    service.session(browser, csrf)
    challenge = service.login_challenge(browser, csrf, owner)
    service.login_verify(browser, csrf, challenge["session_id"], owner)
    return browser, csrf


def _call_result(owner: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[],
        structuredContent={"owner": owner},
    )


def test_shutdown_waits_for_active_mcp_call_before_closing_canary(
    service, monkeypatch
):
    browser, _csrf = _login(service, "owner-a", OWNER_A)
    canary = LifecycleCanary.created[-1]
    started = threading.Event()
    release = threading.Event()
    close_started = threading.Event()
    saw_closed = []
    dispatch_errors: list[BaseException] = []

    async def blocked_call(*, access_service, access_token, **_kwargs):
        principal = access_service.authenticate(access_token)
        started.set()
        await asyncio.to_thread(release.wait, 5)
        saw_closed.append(canary.closed)
        return _call_result(principal.owner)

    from examples.monad_commerce import hosted_mcp

    monkeypatch.setattr(hosted_mcp, "call_hosted_tool", blocked_call)

    def dispatch() -> None:
        try:
            service.dispatch(browser, None, "search", {"query": "csv"})
        except BaseException as exc:  # pragma: no cover - asserted below
            dispatch_errors.append(exc)

    worker = threading.Thread(target=dispatch)
    worker.start()
    assert started.wait(2)

    def close_service() -> None:
        close_started.set()
        service.__exit__(None, None, None)

    closer = threading.Thread(target=close_service)
    closer.start()
    assert close_started.wait(2)
    try:
        assert not canary.closed, "shutdown closed an active serialized MCP call"
    finally:
        release.set()
        worker.join(3)
        closer.join(3)
    assert not worker.is_alive()
    assert not closer.is_alive()
    assert not dispatch_errors
    assert saw_closed == [False]
    assert canary.closed
    assert service._agent_credentials == {}


def test_concurrent_owner_calls_keep_mcp_identity_bound_to_owner(
    service, monkeypatch
):
    sessions = {
        "a": _login(service, "owner-a", OWNER_A),
        "b": _login(service, "owner-b", OWNER_B),
    }

    async def owned_call(*, access_service, access_token, **_kwargs):
        principal = access_service.authenticate(access_token)
        return _call_result(principal.owner)

    from examples.monad_commerce import hosted_mcp

    monkeypatch.setattr(hosted_mcp, "call_hosted_tool", owned_call)
    barrier = threading.Barrier(3)
    results: dict[str, dict[str, object]] = {}
    errors: list[BaseException] = []

    def run(label: str) -> None:
        try:
            barrier.wait()
            browser, _csrf = sessions[label]
            results[label] = service.dispatch(browser, None, "search", {"query": label})
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(label,)) for label in sessions]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(3)

    assert not errors
    assert results["a"] == {"owner": OWNER_A}
    assert results["b"] == {"owner": OWNER_B}
    assert service._agent_credentials == {}


def test_cache_eviction_waits_until_active_call_has_released_canary(
    service, monkeypatch
):
    service.max_open_tenants = 1
    browser_a, _csrf_a = _login(service, "owner-a", OWNER_A)
    canary_a = LifecycleCanary.created[-1]
    started = threading.Event()
    release = threading.Event()
    saw_closed: list[bool] = []
    dispatch_errors: list[BaseException] = []
    login_errors: list[BaseException] = []
    login_done = threading.Event()

    async def blocked_call(*, access_service, access_token, **_kwargs):
        principal = access_service.authenticate(access_token)
        started.set()
        await asyncio.to_thread(release.wait, 5)
        saw_closed.append(canary_a.closed)
        return _call_result(principal.owner)

    from examples.monad_commerce import hosted_mcp

    monkeypatch.setattr(hosted_mcp, "call_hosted_tool", blocked_call)

    def dispatch() -> None:
        try:
            service.dispatch(browser_a, None, "search", {"query": "a"})
        except BaseException as exc:  # pragma: no cover - asserted below
            dispatch_errors.append(exc)

    def login_b() -> None:
        try:
            _login(service, "owner-b", OWNER_B)
        except BaseException as exc:  # pragma: no cover - asserted below
            login_errors.append(exc)
        finally:
            login_done.set()

    worker = threading.Thread(target=dispatch)
    worker.start()
    assert started.wait(2)
    second = threading.Thread(target=login_b)
    second.start()
    assert not login_done.wait(0.2)
    assert not canary_a.closed

    release.set()
    worker.join(3)
    second.join(3)
    assert not worker.is_alive()
    assert not second.is_alive()
    assert not dispatch_errors
    assert not login_errors
    assert saw_closed == [False]
    assert canary_a.closed
