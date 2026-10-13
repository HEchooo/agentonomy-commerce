from __future__ import annotations

import inspect
import json
import logging
import sys
import types
from pathlib import Path

import pytest

from examples.monad_commerce import hosted_process
from examples.monad_commerce.operation_diagnostics import (
    capture_device_operation_failure,
    log_operation_failure,
)


def test_log_operation_failure_uses_only_safe_exception_metadata(caplog):
    class SecretError(RuntimeError):
        def __init__(self, message, **attributes):
            super().__init__(message)
            self.__dict__.update(attributes)

    secret = "token=secret-csv-and-signature"

    def level_one():
        def level_two():
            raise SecretError(
                f"message={secret}",
                secret_csv="transaction_id,amount,999",
                credentials="access-key",
            )

        level_two()

    with pytest.raises(SecretError) as caught:
        level_one()

    caplog.set_level(logging.WARNING)
    log_operation_failure("execute", caught.value)

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "operation=execute" in message
    assert "exception=SecretError" in message
    assert secret not in message
    assert "transaction_id,amount,999" not in message
    assert "access-key" not in message
    assert str(caught.value) not in message
    assert str(Path(__file__).resolve()) not in message

    frame_text = message.split(" traceback=", 1)[1]
    assert len(frame_text.split(",")) <= 8


def test_device_operation_decorator_preserves_signature_identity_and_single_dispatch(caplog):
    error = RuntimeError("private-token-and-csv")

    class Service:
        def __init__(self):
            self.calls = 0

        @capture_device_operation_failure
        def device_operation(self, access_token, operation, args):
            self.calls += 1
            raise error

    service = Service()
    caplog.set_level(logging.WARNING)

    with pytest.raises(RuntimeError) as caught:
        service.device_operation("access-token", "execute", {"csv_text": "private-csv"})

    assert caught.value is error
    assert service.calls == 1
    assert list(inspect.signature(Service.device_operation).parameters) == [
        "self", "access_token", "operation", "args"
    ]
    message = caplog.records[-1].getMessage()
    assert "operation=execute" in message
    assert "private-token-and-csv" not in message
    assert "access-token" not in message
    assert "private-csv" not in message


def test_arbitrary_operation_is_logged_as_outside_scope(caplog):
    error = ValueError("untrusted operation details")
    caplog.set_level(logging.WARNING)

    log_operation_failure("user_supplied_token_and_csv", error)

    message = caplog.records[-1].getMessage()
    assert "operation=outside_scope" in message
    assert "user_supplied_token_and_csv" not in message
    assert "untrusted operation details" not in message


def test_market_worker_logs_exception_without_changing_stdout_json(monkeypatch, tmp_path, caplog, capsys):
    secret = "worker-private-token-and-csv"

    class FakeRuntime:
        def __init__(self, state_dir, port, bootstrap):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, **arguments):
            self.calls += 1
            raise RuntimeError(secret)

    runtime_module = types.ModuleType("examples.monad_commerce.hosted_market_runtime")
    runtime_module.HostedCommerceRuntime = FakeRuntime
    monkeypatch.setitem(sys.modules, "examples.monad_commerce.hosted_market_runtime", runtime_module)
    monkeypatch.setattr(hosted_process, "validate_hosted_bootstrap", lambda value: None)

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def bind(self, address):
            assert address[0] == "127.0.0.1"

        def getsockname(self):
            return ("127.0.0.1", 32123)

    monkeypatch.setattr(hosted_process.socket, "socket", lambda: FakeSocket())

    class InputBuffer:
        def __init__(self, lines):
            self.lines = iter(lines)

        def readline(self, _limit):
            return next(self.lines, b"")

    class Input:
        def __init__(self, lines):
            self.buffer = InputBuffer(lines)

    monkeypatch.setattr(
        hosted_process.sys,
        "stdin",
        Input([
            (json.dumps({"id": 1, "method": "initialize", "arguments": {}}) + "\n").encode(),
            (json.dumps({
                "id": 2,
                "method": "execute",
                "arguments": {"preview_id": "preview_safe"},
            }) + "\n").encode(),
        ]),
    )
    caplog.set_level(logging.WARNING)

    assert hosted_process._market_main(tmp_path) == 0

    stdout_lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert stdout_lines == [
        {"id": 1, "result": {"status": "ready", "mode": "monad_testnet"}},
        {"id": 2, "error": "Operation unconfirmed; query original order"},
    ]
    assert any("operation=execute" in record.getMessage() for record in caplog.records)
    assert all(secret not in record.getMessage() for record in caplog.records)
