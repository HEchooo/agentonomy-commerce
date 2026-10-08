from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.monad_commerce import hosted_server


ORIGIN = "https://commerce.example"


def _private_config(path: Path, value: object = None) -> Path:
    if value is None:
        value = {"safe": True}
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_load_configuration_accepts_only_private_regular_file(tmp_path: Path):
    path = _private_config(tmp_path / "config.json")

    assert hosted_server.load_configuration(path) == {"safe": True}


@pytest.mark.parametrize("kind", ["mode", "symlink", "hardlink", "malformed"])
def test_load_configuration_rejects_unsafe_or_malformed_file(tmp_path: Path, kind: str):
    source = _private_config(tmp_path / "source.json", {"marker": "private-test-marker"})
    if kind == "mode":
        path = source
        path.chmod(0o644)
    elif kind == "symlink":
        path = tmp_path / "config.json"
        path.symlink_to(source)
    elif kind == "hardlink":
        path = tmp_path / "config.json"
        path.hardlink_to(source)
    else:
        path = tmp_path / "config.json"
        path.write_text('{"marker": "private-test-marker"', encoding="utf-8")
        path.chmod(0o600)

    with pytest.raises((OSError, ValueError)):
        hosted_server.load_configuration(path)


def test_validate_only_does_not_enter_service_or_start_listener(tmp_path: Path, monkeypatch, capsys):
    config = _private_config(tmp_path / "config.json")
    events: list[object] = []

    class FakeService:
        def __init__(self, state_dir, configuration, *, public_origin, registry=None):
            assert registry is None
            events.append(("construct", state_dir, configuration, public_origin))

        def __enter__(self):
            events.append("enter")
            raise AssertionError("validate-only must not enter the runtime")

        def __exit__(self, *_args):
            events.append("exit")

    monkeypatch.setattr(hosted_server, "HostedCommerceService", FakeService)
    monkeypatch.setattr(
        hosted_server,
        "create_app",
        lambda **_kwargs: events.append("app") or object(),
    )
    monkeypatch.setattr(
        hosted_server.uvicorn,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("validate-only must not listen")
        ),
    )

    result = hosted_server.main(
        [
            "--config",
            str(config),
            "--state-dir",
            str(tmp_path / "state"),
            "--origin",
            ORIGIN,
            "--validate-only",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert [event for event in events if event in ("enter", "exit")] == []
    assert any(isinstance(event, tuple) and event[0] == "construct" for event in events)
    assert "app" in events
    assert '"credentials_loaded": false' in output
    assert '"broadcast": false' in output
    assert '"listener": "127.0.0.1"' in output


def test_main_redacts_malformed_configuration_error(tmp_path: Path, capsys):
    marker = "private-test-secret-marker"
    path = tmp_path / "config.json"
    path.write_text('{"secret": "' + marker + '"', encoding="utf-8")
    path.chmod(0o600)

    result = hosted_server.main(
        [
            "--config",
            str(path),
            "--state-dir",
            str(tmp_path / "state"),
            "--origin",
            ORIGIN,
            "--validate-only",
        ]
    )
    output = capsys.readouterr().out

    assert result == 2
    assert '"status": "blocked"' in output
    assert marker not in output
