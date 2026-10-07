from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from examples.monad_commerce import hosted_signer


ROLE_ARN = "arn:aws:iam::123456789012:role/agentonomy-dev-kms-runtime"
OWNER_A = "0x" + "44" * 20
OWNER_B = "0x" + "77" * 20


def configuration(owner: str = OWNER_A) -> dict:
    return {
        "profile": "temporary-runtime",
        "region": "ap-southeast-1",
        "account_id": "123456789012",
        "expected_role_arn": ROLE_ARN,
        "credential_directory": "/var/lib/agentonomy-sign/credentials",
        "execution_key_arn": (
            "arn:aws:kms:ap-southeast-1:123456789012:key/"
            "11111111-1111-4111-8111-111111111111"
        ),
        "gas_key_arn": (
            "arn:aws:kms:ap-southeast-1:123456789012:key/"
            "22222222-2222-4222-8222-222222222222"
        ),
        "scope": {
            "network": {
                "mode": "monad_testnet",
                "chain_id": 10143,
                "rpc_urls": ["https://rpc.example.org", "https://rpc.example.net"],
                "token": "0x" + "11" * 20,
                "executor": "0x" + "22" * 20,
                "payee": "0x" + "33" * 20,
            },
            "owner": owner,
            "execution_address": "0x" + "55" * 20,
            "relayer_address": "0x" + "66" * 20,
            "nonce_min": 2,
            "nonce_max": 5,
        },
    }


def write_pinned(path: Path, value: dict) -> Path:
    path.mkdir(mode=0o700)
    file_path = path / "signer.json"
    file_path.write_text(json.dumps(value), encoding="utf-8")
    file_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return file_path


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


def test_linux_bridge_uses_fixed_sudo_command_without_fallback(monkeypatch):
    calls = []
    process = FakeProcess()

    def fake_popen(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return process

    monkeypatch.setattr(hosted_signer.sys, "platform", "linux")
    monkeypatch.setattr(hosted_signer, "_validate_linux_launcher", lambda: None)
    monkeypatch.setattr(hosted_signer.subprocess, "Popen", fake_popen)
    bridge = hosted_signer.HostedKmsSignerBridge(configuration())
    bridge._start()
    payload = process.stdin.getvalue()
    bridge.close()

    assert calls[0][0] == [
        "/usr/bin/sudo",
        "-n",
        "-u",
        "agentonomy-sign",
        "--",
        "/usr/local/libexec/agentonomy-monad-sign",
    ]
    assert calls[0][1]["cwd"] == "/"
    assert calls[0][1]["env"] == {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONUNBUFFERED": "1",
    }
    assert json.loads(payload) == configuration()


def test_linux_launcher_validation_failure_does_not_fallback(monkeypatch):
    monkeypatch.setattr(hosted_signer.sys, "platform", "linux")
    monkeypatch.setattr(
        hosted_signer,
        "_validate_linux_launcher",
        lambda: (_ for _ in ()).throw(RuntimeError("unsafe launcher")),
    )
    popen = lambda *args, **kwargs: pytest.fail("unsafe launcher must fail closed")
    monkeypatch.setattr(hosted_signer.subprocess, "Popen", popen)
    bridge = hosted_signer.HostedKmsSignerBridge(configuration())
    with pytest.raises(RuntimeError, match="unsafe launcher"):
        bridge._start()


def test_darwin_requires_explicit_loopback_development_opt_in(monkeypatch):
    called = []

    def original_start(self):
        called.append(True)

    monkeypatch.setattr(hosted_signer.KmsSignerBridge, "_start", original_start)
    monkeypatch.setattr(hosted_signer.sys, "platform", "darwin")

    bridge = hosted_signer.HostedKmsSignerBridge(configuration())
    with pytest.raises(RuntimeError, match="loopback development"):
        bridge._start()
    assert called == []

    development_bridge = hosted_signer.HostedKmsSignerBridge(
        configuration(), development_loopback=True
    )
    development_bridge._start()
    assert called == [True]


def test_linux_launcher_path_requires_root_owned_single_link_regular_file(tmp_path, monkeypatch):
    monkeypatch.setattr(hosted_signer, "ROOT_UID", os.getuid())
    real_lstat = os.lstat

    def root_owned_lstat(path):
        metadata = real_lstat(path)
        values = list(metadata)
        values[4] = hosted_signer.ROOT_UID
        return os.stat_result(values)

    monkeypatch.setattr(hosted_signer.os, "lstat", root_owned_lstat)
    root_dir = tmp_path / "root" / "libexec"
    root_dir.mkdir(mode=0o700, parents=True)
    launcher = root_dir / "agentonomy-monad-sign"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o700)
    monkeypatch.setattr(hosted_signer, "LINUX_LAUNCHER_PATH", launcher)
    hosted_signer._validate_linux_launcher()

    launcher.chmod(0o702)
    with pytest.raises(ValueError, match="launcher"):
        hosted_signer._validate_linux_launcher()

    launcher.chmod(0o700)
    second_link = root_dir / "second-link"
    os.link(launcher, second_link)
    with pytest.raises(ValueError, match="launcher"):
        hosted_signer._validate_linux_launcher()

    launcher.unlink()
    launcher.symlink_to(second_link)
    with pytest.raises(ValueError, match="launcher"):
        hosted_signer._validate_linux_launcher()


def test_pinned_config_allows_only_canonical_owner_replacement(tmp_path, monkeypatch):
    pinned = configuration()
    config_path = write_pinned(tmp_path / "config-dir", pinned)
    monkeypatch.setattr(hosted_signer, "PINNED_CONFIGURATION_PATH", config_path)

    incoming = configuration(OWNER_B)
    hosted_signer._validate_pinned_configuration(incoming)

    for mutation in (
        {"profile": "agentonomy-dev"},
        {"region": "us-east-1"},
        {"credential_directory": "/tmp/other"},
        {"execution_key_arn": pinned["gas_key_arn"]},
        {"scope": {**incoming["scope"], "nonce_max": 6}},
        {"scope": {**incoming["scope"], "owner": "0x" + "00" * 20}},
        {"scope": {**incoming["scope"], "owner": OWNER_B.upper()}},
    ):
        changed = copy.deepcopy(incoming)
        changed.update(mutation)
        with pytest.raises(ValueError):
            hosted_signer._validate_pinned_configuration(changed)


def test_pinned_config_rejects_operator_profile_before_worker(tmp_path, monkeypatch):
    pinned_path = write_pinned(tmp_path / "config-dir", configuration())
    monkeypatch.setattr(hosted_signer, "PINNED_CONFIGURATION_PATH", pinned_path)
    changed = configuration()
    changed["profile"] = "agentonomy-dev"
    with pytest.raises(ValueError):
        hosted_signer._validate_pinned_configuration(changed)


@pytest.mark.parametrize("kind", ["missing", "symlink", "mode", "malformed"])
def test_pinned_config_is_protected_and_malformed_input_fails_closed(
    tmp_path, monkeypatch, kind
):
    config_dir = tmp_path / "config-dir"
    pinned_path = write_pinned(config_dir, configuration())
    monkeypatch.setattr(hosted_signer, "PINNED_CONFIGURATION_PATH", pinned_path)
    if kind == "missing":
        pinned_path.unlink()
    elif kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text(json.dumps(configuration()), encoding="utf-8")
        target.chmod(0o600)
        pinned_path.unlink()
        pinned_path.symlink_to(target)
    elif kind == "mode":
        pinned_path.chmod(0o640)
    elif kind == "malformed":
        pinned_path.write_text("{not-json\n", encoding="utf-8")
        pinned_path.chmod(0o600)
    with pytest.raises(ValueError):
        hosted_signer._read_pinned_configuration()


def test_operator_environment_does_not_forward_aws_or_import_state(monkeypatch):
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "PYTHONPATH",
        "PYTHONHOME",
        "HOME",
    ):
        monkeypatch.setenv(name, "must-not-forward")
    env = hosted_signer._operator_environment()
    assert env == {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONUNBUFFERED": "1",
    }


def test_entry_replays_verified_bootstrap_to_canonical_worker_and_restores_stdin(
    tmp_path, monkeypatch
):
    pinned_path = write_pinned(tmp_path / "config-dir", configuration())
    monkeypatch.setattr(hosted_signer, "PINNED_CONFIGURATION_PATH", pinned_path)
    monkeypatch.setattr(hosted_signer, "_require_signer_identity", lambda: None)
    incoming = configuration(OWNER_B)
    stream = io.StringIO(
        json.dumps(incoming) + "\n" + json.dumps({"id": 4, "method": "health", "params": {}}) + "\n"
    )
    output = io.StringIO()
    seen = []

    def fake_worker_main():
        seen.append(hosted_signer.sys.stdin.readline())
        seen.append(hosted_signer.sys.stdin.readline())
        return 17

    monkeypatch.setattr(hosted_signer.signer_process, "worker_main", fake_worker_main)
    monkeypatch.setattr(hosted_signer.sys, "stdin", stream)
    monkeypatch.setattr(hosted_signer.sys, "stdout", output)
    assert hosted_signer.main() == 17
    assert hosted_signer.sys.stdin is stream
    assert json.loads(seen[0]) == incoming
    assert json.loads(seen[1]) == {"id": 4, "method": "health", "params": {}}


def test_entry_rejects_malformed_bootstrap_without_config_or_secret_output(
    tmp_path, monkeypatch
):
    pinned_path = write_pinned(tmp_path / "config-dir", configuration())
    monkeypatch.setattr(hosted_signer, "PINNED_CONFIGURATION_PATH", pinned_path)
    monkeypatch.setattr(hosted_signer, "_require_signer_identity", lambda: None)
    secret = "AWS_SECRET_ACCESS_KEY=must-not-escape"
    monkeypatch.setattr(hosted_signer.sys, "stdin", io.StringIO(secret + "\n"))
    output = io.StringIO()
    monkeypatch.setattr(hosted_signer.sys, "stdout", output)
    assert hosted_signer.main() == 2
    assert "must-not-escape" not in output.getvalue()
    assert "signer unavailable" in output.getvalue()


def test_launcher_script_is_fixed_and_does_not_forward_arguments():
    script = Path("packaging/monad/agentonomy-monad-sign").read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n")
    assert "env -i" in script
    assert "/opt/agentonomy-commerce/current/.venv/bin/python" in script
    assert "-I" in script
    assert "/opt/agentonomy-commerce/current/examples/monad_commerce/hosted_signer.py" in script
    assert '"$@"' not in script
    assert "AWS_" not in script
