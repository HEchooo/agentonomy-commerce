#!/usr/bin/env python3
"""Install one bounded AWS assumed-role session for the hosted review signer.

The operator supplies the output of a protected ``AssumeRole`` call on stdin.
This helper validates the short-lived session, installs the SDK profile used by
the isolated signer account, and keeps root-owned backups of changed files. It
does not mint credentials, start services, sign transactions, or broadcast.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import sys
import tempfile
from typing import Any


HOSTNAME = "agentonomy-commerce-review-01"
REGION = "ap-southeast-1"
PROFILE = "agentonomy-commerce-monad-role"
SERVICE_USER = "agentonomy-sign"
CREDENTIAL_ROOT = Path("/var/lib/agentonomy-sign/aws")
BACKUP_ROOT = Path("/root/agentonomy-commerce-session-backups")
SIGNER_CONFIG_PATH = Path("/etc/agentonomy-commerce/signer.json")
MAX_INPUT_BYTES = 32768
MAX_SIGNER_CONFIG_BYTES = 65536
MIN_SESSION_TTL = timedelta(hours=1)
MAX_SESSION_TTL = timedelta(hours=12, seconds=300)
BACKUP_PREFIX = "agentonomy-commerce-session-backup-"
EXPECTED_ROLE_NAME = "agentonomy-dev-kms-runtime"
_IAM_ROLE_RE = re.compile(
    rf"arn:aws:iam::(?P<account>[0-9]{{12}}):role/{EXPECTED_ROLE_NAME}\Z"
)
_ACCESS_KEY = re.compile(r"ASIA[A-Z0-9]{16}\Z")


def _invalid_session() -> ValueError:
    return ValueError("invalid temporary session")


def _strict_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and not any(
        character.isspace() for character in value
    )


def _utc_datetime(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError
    return parsed


def _assumed_role_prefix(expected_role_arn: object) -> str:
    if not isinstance(expected_role_arn, str):
        raise ValueError
    match = _IAM_ROLE_RE.fullmatch(expected_role_arn)
    if match is None:
        raise ValueError
    return (
        f"arn:aws:sts::{match.group('account')}:assumed-role/"
        f"{EXPECTED_ROLE_NAME}/"
    )


def validate_session(
    raw: bytes, *, now: datetime, expected_role_arn: str
) -> dict[str, object]:
    """Validate a bounded STS AssumeRole result without exposing its secrets."""

    try:
        assumed_role_prefix = _assumed_role_prefix(expected_role_arn)
        if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_INPUT_BYTES:
            raise ValueError
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError
        document = json.loads(bytes(raw))
        if not isinstance(document, dict):
            raise ValueError
        credentials = document["Credentials"]
        role_user = document["AssumedRoleUser"]
        if not isinstance(credentials, dict) or not isinstance(role_user, dict):
            raise ValueError
        values = {
            key: credentials[key]
            for key in ("AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration")
        }
        role_arn = role_user["Arn"]
        if not all(_strict_text(value) for value in values.values()):
            raise ValueError
        if not _ACCESS_KEY.fullmatch(values["AccessKeyId"]):
            raise ValueError
        if not _strict_text(role_arn) or not role_arn.startswith(assumed_role_prefix):
            raise ValueError
        if not role_arn[len(assumed_role_prefix) :]:
            raise ValueError
        expiration = _utc_datetime(values["Expiration"])
        remaining = expiration - now
        if not MIN_SESSION_TTL < remaining <= MAX_SESSION_TTL:
            raise ValueError
        return {
            "credentials": values,
            "role_arn": role_arn,
            "expiration": values["Expiration"],
        }
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise _invalid_session() from None


def _read_expected_role_arn() -> str:
    """Read and validate the non-secret role pin owned by the signer user."""

    try:
        account = pwd.getpwnam(SERVICE_USER)
        if account.pw_uid == 0:
            raise ValueError
        path = _absolute(SIGNER_CONFIG_PATH)
        if path.name != "signer.json":
            raise ValueError
        parent = path.parent
        _require_real_ancestors(parent, "signer configuration directory")
        parent_info = parent.lstat()
        if (
            stat.S_ISLNK(parent_info.st_mode)
            or not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != account.pw_uid
            or parent_info.st_gid != account.pw_gid
            or stat.S_IMODE(parent_info.st_mode) != 0o700
        ):
            raise ValueError
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != account.pw_uid
                or metadata.st_gid != account.pw_gid
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > MAX_SIGNER_CONFIG_BYTES
            ):
                raise ValueError
            raw_chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(
                    descriptor,
                    min(65536, MAX_SIGNER_CONFIG_BYTES + 1 - total),
                )
                if not chunk:
                    break
                raw_chunks.append(chunk)
                total += len(chunk)
                if total > MAX_SIGNER_CONFIG_BYTES:
                    raise ValueError
            raw = b"".join(raw_chunks)
        finally:
            os.close(descriptor)
        configuration = json.loads(raw.decode("utf-8"))
        if not isinstance(configuration, dict):
            raise ValueError
        # This canonical validator checks the complete non-secret signer pin;
        # it does not load AWS credentials or start the signing worker.
        from agentonomy_commerce.signer_process import validate_configuration

        validate_configuration(configuration)
        expected_role_arn = configuration["expected_role_arn"]
        _assumed_role_prefix(expected_role_arn)
        return expected_role_arn
    except Exception:
        raise ValueError("unsafe signer configuration") from None


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _require_real_ancestors(path: Path, label: str) -> None:
    """Reject symlinks in every existing component of a lexical path."""

    absolute = _absolute(path)
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ValueError(f"unsafe {label}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"unsafe {label}")


def _ensure_directory(path: Path, *, mode: int, uid: int, gid: int, label: str) -> None:
    _require_real_ancestors(path, label)
    created = False
    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(parents=True, mode=mode, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"unsafe {label}") from exc
        created = True
        _require_real_ancestors(path, label)
        try:
            info = path.lstat()
        except OSError as exc:
            raise ValueError(f"unsafe {label}") from exc
    except OSError as exc:
        raise ValueError(f"unsafe {label}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"unsafe {label}")
    if created:
        try:
            os.chmod(path, mode)
            os.chown(path, uid, gid)
        except OSError as exc:
            raise ValueError(f"unsafe {label}") from exc
    elif (
        info.st_uid != uid
        or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) != mode
    ):
        raise ValueError(f"unsafe {label}")


def _existing_regular(path: Path, label: str) -> os.stat_result | None:
    _require_real_ancestors(path, label)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"unsafe {label}") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
    ):
        raise ValueError(f"unsafe {label}")
    return info


def _read_regular(path: Path, label: str) -> bytes:
    before = _existing_regular(path, label)
    if before is None:
        raise ValueError(f"unsafe {label}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"unsafe {label}") from exc
    try:
        after = os.fstat(fd)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
        ):
            raise ValueError(f"unsafe {label}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise ValueError(f"unsafe {label}") from exc
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError("unable to sync protected directory") from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        raise ValueError("unable to sync protected directory") from exc
    finally:
        os.close(fd)


def _atomic_write(
    path: Path,
    content: bytes,
    *,
    mode: int,
    uid: int,
    gid: int,
    label: str,
) -> None:
    _require_real_ancestors(path, label)
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError(f"unsafe {label}")
    _existing_regular(path, label)
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    except OSError as exc:
        raise ValueError(f"unsafe {label}") from exc
    temporary = Path(temporary_name)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        view = memoryview(content)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        # Recheck before replacement. A symlink or hardlink is never silently
        # followed or accepted as the protected destination.
        _existing_regular(path, label)
        os.replace(temporary, path)
        os.chmod(path, mode)
        os.chown(path, uid, gid)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise ValueError(f"unsafe {label}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _backup_existing(
    path: Path,
    backup: Path,
    *,
    backup_name: str,
    backup_uid: int,
    backup_gid: int,
) -> None:
    data = _read_regular(path, "backup source")
    target = backup / backup_name
    if _existing_regular(target, "backup target") is not None:
        raise ValueError("backup target already exists")
    _atomic_write(
        target,
        data,
        mode=0o600,
        uid=backup_uid,
        gid=backup_gid,
        label="backup target",
    )


def install_file(
    path: Path,
    content: str,
    *,
    mode: int,
    uid: int,
    gid: int,
    backup: Path,
    backup_name: str | None = None,
    backup_uid: int | None = None,
    backup_gid: int | None = None,
) -> bool:
    """Atomically install protected text, backing up changed contents."""

    if not isinstance(content, str):
        raise TypeError("file content must be text")
    _require_real_ancestors(path, "install target")
    _require_real_ancestors(backup, "backup directory")
    if not backup.is_dir() or backup.is_symlink():
        raise ValueError("unsafe backup directory")
    info = _existing_regular(path, "install target")
    encoded = content.encode("utf-8")
    if info is not None:
        old = _read_regular(path, "install target")
        metadata_matches = (
            old == encoded
            and stat.S_IMODE(info.st_mode) == mode
            and info.st_uid == uid
            and info.st_gid == gid
        )
        if metadata_matches:
            return False
        if old != encoded:
            _backup_existing(
                path,
                backup,
                backup_name=backup_name or path.name,
                backup_uid=backup_uid if backup_uid is not None else os.getuid(),
                backup_gid=backup_gid if backup_gid is not None else os.getgid(),
            )
    _atomic_write(path, encoded, mode=mode, uid=uid, gid=gid, label="install target")
    return True


def _new_backup_directory(parent: Path, *, uid: int, gid: int) -> Path:
    parent = _absolute(parent)
    _require_real_ancestors(parent, "backup parent")
    if not parent.exists():
        _ensure_directory(parent, mode=0o700, uid=uid, gid=gid, label="backup parent")
    try:
        info = parent.lstat()
    except OSError as exc:
        raise ValueError("unsafe backup parent") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != uid
        or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("unsafe backup parent")
    try:
        directory = Path(tempfile.mkdtemp(prefix=BACKUP_PREFIX, dir=parent))
        os.chmod(directory, 0o700)
        os.chown(directory, uid, gid)
    except OSError as exc:
        raise ValueError("unable to create backup directory") from exc
    _require_real_ancestors(directory, "backup directory")
    return directory


def _operator_directory(path: Path, label: str) -> None:
    _require_real_ancestors(path, label)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(f"unsafe {label}") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError(f"unsafe {label}")


def install_session(
    raw: bytes,
    *,
    now: datetime | None = None,
    expected_role_arn: str,
    credential_root: Path = CREDENTIAL_ROOT,
    backup_root: Path = BACKUP_ROOT,
    service_uid: int | None = None,
    service_gid: int | None = None,
    root_uid: int | None = None,
    root_gid: int | None = None,
) -> dict[str, Any]:
    """Install the fixed signer profile using caller-supplied test roots."""

    if now is None:
        now = datetime.now(timezone.utc)
    session = validate_session(
        raw, now=now, expected_role_arn=expected_role_arn
    )
    credential_root = _absolute(Path(credential_root))
    backup_root = _absolute(Path(backup_root))
    if root_uid is None:
        root_uid = os.getuid()
    if root_gid is None:
        root_gid = os.getgid()
    if service_uid is None or service_gid is None:
        account = pwd.getpwnam(SERVICE_USER)
        service_uid = account.pw_uid if service_uid is None else service_uid
        service_gid = account.pw_gid if service_gid is None else service_gid
    if service_uid == 0:
        raise ValueError("signer service account must be non-root")

    _ensure_directory(
        credential_root,
        mode=0o700,
        uid=service_uid,
        gid=service_gid,
        label="credential directory",
    )
    backup = _new_backup_directory(backup_root, uid=root_uid, gid=root_gid)
    credentials = session["credentials"]
    assert isinstance(credentials, dict)
    credentials_text = (
        f"[{PROFILE}]\n"
        f"aws_access_key_id = {credentials['AccessKeyId']}\n"
        f"aws_secret_access_key = {credentials['SecretAccessKey']}\n"
        f"aws_session_token = {credentials['SessionToken']}\n"
    )
    config_text = f"[profile {PROFILE}]\nregion = {REGION}\n"
    info_text = json.dumps(
        {"role_arn": session["role_arn"], "expiration": session["expiration"]},
        separators=(",", ":"),
    ) + "\n"
    for filename, content in (
        ("credentials", credentials_text),
        ("config", config_text),
        ("session-info.json", info_text),
    ):
        install_file(
            credential_root / filename,
            content,
            mode=0o600,
            uid=service_uid,
            gid=service_gid,
            backup=backup,
            backup_name=filename,
            backup_uid=root_uid,
            backup_gid=root_gid,
        )
    return {"status": "installed", "expires_at": session["expiration"]}


def main(argv: list[str] | None = None) -> int:
    if argv:
        print("Agentonomy Commerce AWS session install failed.", file=sys.stderr)
        return 1
    if os.geteuid() != 0 or socket.gethostname() != HOSTNAME:
        print(
            "Agentonomy Commerce AWS session install failed: root on the review host is required.",
            file=sys.stderr,
        )
        return 1
    try:
        account = pwd.getpwnam(SERVICE_USER)
        if account.pw_uid == 0:
            raise ValueError("invalid signer service account")
        if not BACKUP_ROOT.exists():
            _ensure_directory(BACKUP_ROOT, mode=0o700, uid=0, gid=0, label="backup parent")
        _operator_directory(BACKUP_ROOT, "backup parent")
        expected_role_arn = _read_expected_role_arn()
        result = install_session(
            sys.stdin.buffer.read(MAX_INPUT_BYTES + 1),
            now=datetime.now(timezone.utc),
            expected_role_arn=expected_role_arn,
            credential_root=CREDENTIAL_ROOT,
            backup_root=BACKUP_ROOT,
            service_uid=account.pw_uid,
            service_gid=account.pw_gid,
            root_uid=0,
            root_gid=0,
        )
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (OSError, KeyError, TypeError, ValueError):
        print(
            "Agentonomy Commerce AWS session install failed; check input, role, host and protected paths.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
