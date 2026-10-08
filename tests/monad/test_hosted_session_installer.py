from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from scripts.monad import install_hosted_session as installer


NOW = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
EXPECTED_ROLE_ARN = "arn:aws:iam::123456789012:role/agentonomy-dev-kms-runtime"
ROLE_ARN = (
    "arn:aws:sts::123456789012:assumed-role/"
    "agentonomy-dev-kms-runtime/hosted-review-20261008"
)


def session_document(
    *,
    expiration: str = "2026-10-08T02:30:00Z",
    access_key: str = "ASIAABCDEFGHIJKLMNOP",
    role_arn: str = ROLE_ARN,
) -> bytes:
    return json.dumps(
        {
            "Credentials": {
                "AccessKeyId": access_key,
                "SecretAccessKey": "secret-value",
                "SessionToken": "session-token-value",
                "Expiration": expiration,
            },
            "AssumedRoleUser": {
                "Arn": role_arn,
                "AssumedRoleId": "AROATEST:hosted-review-20261008",
            },
        }
    ).encode()


def install_kwargs(tmp_path: Path) -> dict:
    uid = os.getuid()
    gid = os.getgid()
    return {
        "credential_root": tmp_path / "var" / "lib" / "agentonomy-sign" / "aws",
        "backup_root": tmp_path / "root" / "agentonomy-commerce-session-backups",
        "service_uid": uid,
        "service_gid": gid,
        "root_uid": uid,
        "root_gid": gid,
        "expected_role_arn": EXPECTED_ROLE_ARN,
    }


def test_validate_session_requires_fixed_assumed_role_and_bounded_utc_expiry():
    parsed = installer.validate_session(
        session_document(), now=NOW, expected_role_arn=EXPECTED_ROLE_ARN
    )
    assert parsed["role_arn"] == ROLE_ARN
    assert parsed["credentials"]["AccessKeyId"] == "ASIAABCDEFGHIJKLMNOP"
    assert parsed["expiration"] == "2026-10-08T02:30:00Z"

    cases = [
        session_document(expiration="2026-10-08T01:00:00Z"),
        session_document(expiration="2026-10-08T12:06:00Z"),
        session_document(access_key="AKIA" + "ABCDEFGHIJKLMNOP"),
    ]
    wrong_role = json.loads(session_document().decode())
    wrong_role["AssumedRoleUser"]["Arn"] = (
        "arn:aws:sts::123456789012:assumed-role/other-role/session"
    )
    cases.append(json.dumps(wrong_role).encode())
    for raw in cases:
        with pytest.raises(ValueError, match="invalid temporary session"):
            installer.validate_session(
                raw, now=NOW, expected_role_arn=EXPECTED_ROLE_ARN
            )


@pytest.mark.parametrize(
    "expected_role_arn",
    [
        "arn:aws:iam::123456789012:role/other-runtime",
        "arn:aws:iam::12345678901:role/agentonomy-dev-kms-runtime",
        "arn:aws:iam::123456789012:user/agentonomy-dev-kms-runtime",
    ],
)
def test_validate_session_rejects_wrong_expected_role_pin(expected_role_arn):
    with pytest.raises(ValueError, match="invalid temporary session"):
        installer.validate_session(
            session_document(), now=NOW, expected_role_arn=expected_role_arn
        )


def test_validate_session_rejects_role_account_mismatch():
    raw = session_document(
        role_arn=(
            "arn:aws:sts::999999999999:assumed-role/"
            "agentonomy-dev-kms-runtime/hosted-review-20261008"
        )
    )
    with pytest.raises(ValueError, match="invalid temporary session"):
        installer.validate_session(
            raw, now=NOW, expected_role_arn=EXPECTED_ROLE_ARN
        )


@pytest.mark.parametrize(
    "field",
    ["AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration"],
)
def test_validate_session_rejects_empty_or_whitespace_credentials(field):
    document = json.loads(session_document().decode())
    document["Credentials"][field] = " "
    with pytest.raises(ValueError, match="invalid temporary session"):
        installer.validate_session(
            json.dumps(document).encode(),
            now=NOW,
            expected_role_arn=EXPECTED_ROLE_ARN,
        )


def test_validate_session_rejects_oversized_stdin_payload():
    with pytest.raises(ValueError, match="invalid temporary session"):
        installer.validate_session(
            b"{" + b"x" * installer.MAX_INPUT_BYTES,
            now=NOW,
            expected_role_arn=EXPECTED_ROLE_ARN,
        )


def test_install_writes_signer_profile_and_secret_free_session_info(tmp_path):
    result = installer.install_session(
        session_document(), now=NOW, **install_kwargs(tmp_path)
    )
    assert set(result) == {"status", "expires_at"}
    assert result["status"] == "installed"
    root = tmp_path / "var" / "lib" / "agentonomy-sign" / "aws"
    credentials = root / "credentials"
    config = root / "config"
    info = root / "session-info.json"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    for path in (credentials, config, info):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert credentials.read_text() == (
        "[agentonomy-commerce-monad-role]\n"
        "aws_access_key_id = ASIAABCDEFGHIJKLMNOP\n"
        "aws_secret_access_key = secret-value\n"
        "aws_session_token = session-token-value\n"
    )
    assert config.read_text() == (
        "[profile agentonomy-commerce-monad-role]\n"
        "region = ap-southeast-1\n"
    )
    assert json.loads(info.read_text()) == {
        "role_arn": ROLE_ARN,
        "expiration": "2026-10-08T02:30:00Z",
    }
    assert "secret-value" not in info.read_text()
    assert "session-token-value" not in info.read_text()


def test_changed_files_are_backed_up_and_same_content_is_idempotent(tmp_path):
    kwargs = install_kwargs(tmp_path)
    installer.install_session(session_document(), now=NOW, **kwargs)
    changed = installer.install_session(
        session_document(access_key="ASIAQRSTUVWXYZABCDEF"),
        now=NOW,
        **kwargs,
    )
    assert changed["status"] == "installed"
    backup_parent = kwargs["backup_root"]
    backups = sorted(backup_parent.glob("agentonomy-commerce-session-backup-*"))
    assert len(backups) == 2
    non_empty = [backup for backup in backups if any(backup.iterdir())]
    assert len(non_empty) == 1
    files = list(non_empty[0].iterdir())
    assert files
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)
    assert any("ASIAABCDEFGHIJKLMNOP" in path.read_text() for path in files)

    before = set(backup_parent.iterdir())
    installer.install_session(
        session_document(access_key="ASIAQRSTUVWXYZABCDEF"), now=NOW, **kwargs
    )
    after = set(backup_parent.iterdir())
    assert len(after) == len(before) + 1
    newest = after - before
    assert len(newest) == 1
    assert not any(newest.pop().iterdir())


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_install_rejects_unsafe_existing_credential_file(tmp_path, kind):
    kwargs = install_kwargs(tmp_path)
    root = kwargs["credential_root"]
    root.mkdir(mode=0o700, parents=True)
    target = tmp_path / "target"
    target.write_text("unsafe", encoding="utf-8")
    target.chmod(0o600)
    credentials = root / "credentials"
    if kind == "symlink":
        credentials.symlink_to(target)
    else:
        credentials.write_text("unsafe", encoding="utf-8")
        credentials.chmod(0o600)
        os.link(credentials, root / "credentials-hardlink")
    with pytest.raises(ValueError, match="unsafe"):
        installer.install_session(session_document(), now=NOW, **kwargs)


def test_main_is_root_and_hostname_gated_and_prints_no_credentials(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(installer, "HOSTNAME", "agentonomy-commerce-review-01")
    monkeypatch.setattr(installer.socket, "gethostname", lambda: installer.HOSTNAME)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    monkeypatch.setattr(installer.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    monkeypatch.setattr(installer, "CREDENTIAL_ROOT", tmp_path / "aws")
    monkeypatch.setattr(installer, "BACKUP_ROOT", tmp_path / "backups")
    signer_dir = tmp_path / "etc" / "agentonomy-commerce"
    signer_dir.mkdir(mode=0o700, parents=True)
    signer_config = signer_dir / "signer.json"
    signer_config.write_text(
        json.dumps(
            {
                "profile": "agentonomy-commerce-monad-role",
                "region": "ap-southeast-1",
                "account_id": "123456789012",
                "expected_role_arn": EXPECTED_ROLE_ARN,
                "credential_directory": str(tmp_path / "aws"),
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
                        "rpc_urls": [
                            "https://rpc.example.org",
                            "https://rpc.example.net",
                        ],
                        "token": "0x" + "11" * 20,
                        "executor": "0x" + "22" * 20,
                        "payee": "0x" + "33" * 20,
                    },
                    "owner": "0x" + "44" * 20,
                    "execution_address": "0x" + "55" * 20,
                    "relayer_address": "0x" + "66" * 20,
                    "nonce_min": 2,
                    "nonce_max": 5,
                },
            }
        ),
        encoding="utf-8",
    )
    signer_config.chmod(0o600)
    monkeypatch.setattr(installer, "SIGNER_CONFIG_PATH", signer_config)
    monkeypatch.setattr(installer.os, "chown", lambda *args: None)
    monkeypatch.setattr(installer.os, "fchown", lambda *args: None)
    monkeypatch.setattr(installer, "_operator_directory", lambda *args: None)
    def test_backup_directory(parent, *, uid, gid):
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = parent / "agentonomy-commerce-session-backup-test"
        directory.mkdir(mode=0o700)
        return directory
    monkeypatch.setattr(installer, "_new_backup_directory", test_backup_directory)
    monkeypatch.setattr(installer.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(session_document())))
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(installer, "datetime", FixedDateTime)

    assert installer.main() == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert set(output) == {"status", "expires_at"}
    assert output["status"] == "installed"
    assert "secret-value" not in captured.out


def test_packaging_keeps_sudo_boundary_and_service_loopback_scope():
    service = Path("packaging/monad/agentonomy-commerce.service").read_text()
    sudoers = Path("packaging/monad/agentonomy-commerce.sudoers").read_text()
    assert "User=agentonomy-web" in service
    assert "127.0.0.1" in service and "8092" in service
    assert "https://review.agentonomy.xyz" in service
    assert "UMask=0077" in service
    assert "Restart=on-failure" in service
    assert "ProtectHome=true" in service
    assert "NoNewPrivileges=true" not in service
    assert "PrivateUsers=true" not in service
    assert "AWS_ACCESS_KEY_ID" not in service
    assert "agentonomy-web" in sudoers
    assert "agentonomy-sign" in sudoers
    assert "/usr/local/libexec/agentonomy-monad-sign \"\"" in sudoers
    assert "env_reset" in sudoers


def test_main_rejects_unsafe_signer_pin_without_secret_output(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(installer, "HOSTNAME", "agentonomy-commerce-review-01")
    monkeypatch.setattr(installer.socket, "gethostname", lambda: installer.HOSTNAME)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        installer.pwd,
        "getpwnam",
        lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()),
    )
    unsafe_parent = tmp_path / "real"
    unsafe_parent.mkdir(mode=0o700)
    unsafe_config = unsafe_parent / "signer.json"
    unsafe_config.write_text("{}", encoding="utf-8")
    unsafe_config.chmod(0o600)
    link_parent = tmp_path / "link-parent"
    link_parent.symlink_to(unsafe_parent, target_is_directory=True)
    monkeypatch.setattr(installer, "SIGNER_CONFIG_PATH", link_parent / "signer.json")
    monkeypatch.setattr(installer, "CREDENTIAL_ROOT", tmp_path / "aws")
    monkeypatch.setattr(installer, "BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(installer.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(session_document())))

    assert installer.main() == 1
    captured = capsys.readouterr()
    assert "secret-value" not in captured.out + captured.err
    assert "AWS session install failed" in captured.err
