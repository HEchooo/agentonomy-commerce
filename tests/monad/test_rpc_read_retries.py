import time

import httpx
import pytest

import agentonomy_commerce.budget_network as budget_network
from agentonomy_commerce.budget_network import RpcClient


RPC_URL = "http://127.0.0.1:8545"
READ_METHOD = "eth_blockNumber"
READ_PARAMS = []


def _response(payload=None, *, status_code=200, content=None):
    request = httpx.Request("POST", RPC_URL)
    if content is not None:
        return httpx.Response(status_code, content=content, request=request)
    return httpx.Response(status_code, json=payload, request=request)


class _ScriptedClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def post(self, url, *, json):
        self.requests.append((url, json))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _rpc_with_outcomes(monkeypatch, outcomes):
    client = _ScriptedClient(outcomes)
    monkeypatch.setattr(budget_network.httpx, "Client", lambda **kwargs: client)
    return RpcClient(RPC_URL), client


@pytest.fixture
def sleep_delays(monkeypatch):
    delays = []
    monkeypatch.setattr(time, "sleep", delays.append)
    return delays


def test_read_retries_transport_failure_with_same_request_then_returns_result(monkeypatch, sleep_delays):
    rpc, client = _rpc_with_outcomes(
        monkeypatch,
        [httpx.ReadError("transient"), _response({"jsonrpc": "2.0", "id": 1, "result": "0x2a"})],
    )

    assert rpc.call(READ_METHOD, READ_PARAMS) == "0x2a"
    assert len(client.requests) == 2
    assert client.requests[0] == client.requests[1] == (
        RPC_URL,
        {"jsonrpc": "2.0", "id": 1, "method": READ_METHOD, "params": READ_PARAMS},
    )
    assert sleep_delays == [0.1]


@pytest.mark.parametrize(
    "failed_response",
    [
        _response({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "temporary"}}),
        _response(content=b"not-json"),
    ],
    ids=["jsonrpc-error", "bad-json-framing"],
)
def test_read_retries_failed_response_validation_then_recovers(monkeypatch, sleep_delays, failed_response):
    rpc, client = _rpc_with_outcomes(
        monkeypatch,
        [failed_response, _response({"jsonrpc": "2.0", "id": 1, "result": "0x1"})],
    )

    assert rpc.call(READ_METHOD, READ_PARAMS) == "0x1"
    assert len(client.requests) == 2
    assert sleep_delays == [0.1]


@pytest.mark.parametrize(
    "outcomes,match",
    [
        ([httpx.ReadError("first"), httpx.ReadError("second"), httpx.ReadError("third")], "status may be unknown"),
        (
            [
                _response({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000}}),
                _response({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000}}),
                _response({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000}}),
            ],
            "invalid or failed response",
        ),
    ],
    ids=["transport", "jsonrpc-error"],
)
def test_read_failure_is_exhausted_after_three_attempts(monkeypatch, sleep_delays, outcomes, match):
    rpc, client = _rpc_with_outcomes(monkeypatch, outcomes)

    with pytest.raises(RuntimeError, match=match):
        rpc.call(READ_METHOD, READ_PARAMS)

    assert len(client.requests) == 3
    assert sleep_delays == [0.1, 0.2]


def test_read_success_does_not_issue_an_extra_request(monkeypatch, sleep_delays):
    rpc, client = _rpc_with_outcomes(
        monkeypatch,
        [_response({"jsonrpc": "2.0", "id": 1, "result": "0x7"})],
    )

    assert rpc.call(READ_METHOD, READ_PARAMS) == "0x7"
    assert len(client.requests) == 1
    assert sleep_delays == []


@pytest.mark.parametrize(
    "outcome",
    [
        httpx.ReadTimeout("timeout"),
        _response({"jsonrpc": "2.0", "id": 1, "result": "0x1"}, status_code=503),
        _response({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000}}),
        _response(content=b"not-json"),
    ],
    ids=["timeout", "http-error", "jsonrpc-error", "bad-json-framing"],
)
def test_broadcast_failure_is_never_retried(monkeypatch, outcome):
    rpc, client = _rpc_with_outcomes(monkeypatch, [outcome])
    rpc.writable = True

    with pytest.raises(RuntimeError):
        rpc.call("eth_sendRawTransaction", ["0xdeadbeef"])

    assert len(client.requests) == 1


def test_out_of_scope_method_is_rejected_before_any_network_request(monkeypatch):
    rpc, client = _rpc_with_outcomes(monkeypatch, [])
    rpc.writable = True

    with pytest.raises(ValueError, match="outside this client's scope"):
        rpc.call("eth_sendTransaction", [])

    assert client.requests == []


def test_oversized_response_keeps_the_existing_cap_and_fails_fast(monkeypatch):
    oversized = _response(content=b"x" * 2_000_001)
    rpc, client = _rpc_with_outcomes(monkeypatch, [oversized])

    with pytest.raises(RuntimeError, match="status may be unknown"):
        rpc.call(READ_METHOD, READ_PARAMS)

    assert len(client.requests) == 1
