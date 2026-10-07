"""OS-bounded launcher for the hosted Monad signer.

Linux production uses the fixed root-installed sudo boundary and the
``agentonomy-sign`` service identity.  Darwin is an explicit loopback-only
development escape hatch; it is process isolation, not an OS security
boundary.  The canonical signer worker remains the sole implementation of
configuration, KMS and purchase-signing rules.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import TextIO

# ``-I`` removes user import configuration.  The launcher is installed from a
# root-owned release tree, so this is the only application root the entrypoint
# may add back to ``sys.path``.
_FIXED_CODE_ROOT = Path("/opt/agentonomy-commerce/current")
if str(_FIXED_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_FIXED_CODE_ROOT))

from agentonomy_commerce import signer_process
from agentonomy_commerce.budget_network import address
from agentonomy_commerce.signer_process import KmsSignerBridge


PINNED_CONFIGURATION_PATH = Path("/etc/agentonomy-commerce/signer.json")
LINUX_LAUNCHER_PATH = Path("/usr/local/libexec/agentonomy-monad-sign")
SIGNER_USER = "agentonomy-sign"
ROOT_UID = 0
MAX_LINE = signer_process.MAX_LINE


def _operator_environment() -> dict[str, str]:
    """Return the complete environment allowed for the sudo boundary."""

    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONUNBUFFERED": "1",
    }


def _validate_linux_launcher() -> None:
    """Require the fixed launcher and every parent component to be trusted."""

    path = LINUX_LAUNCHER_PATH
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("unsafe signer launcher")
    try:
        current = Path(path.anchor)
        for part in path.parts[1:-1]:
            current /= part
            metadata = os.lstat(current)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != ROOT_UID
                or stat.S_IMODE(metadata.st_mode) & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise ValueError("unsafe signer launcher")
        metadata = os.lstat(path)
    except (OSError, ValueError):
        raise ValueError("unsafe signer launcher") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != ROOT_UID
        or stat.S_IMODE(metadata.st_mode) & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ValueError("unsafe signer launcher")


def _read_pinned_configuration() -> dict:
    """Read and validate the agentonomy-sign-owned non-secret config."""

    path = PINNED_CONFIGURATION_PATH
    if not path.is_absolute() or path.name != "signer.json":
        raise ValueError("invalid pinned signer configuration path")
    try:
        signer_process._validate_no_symlink_components(path.parent)
        metadata = os.lstat(path.parent)
        signer_process._validate_private_stat(
            metadata,
            path.parent,
            signer_process._PRIVATE_DIRECTORY_MODE,
            "directory",
        )
        contents = signer_process._read_private_file(path, "pinned signer configuration")
        value = json.loads(contents)
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("invalid pinned signer configuration") from None
    if not isinstance(value, dict):
        raise ValueError("invalid pinned signer configuration")
    try:
        signer_process.validate_configuration(value)
    except Exception:
        raise ValueError("invalid pinned signer configuration") from None
    return value


def _canonical_owner(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("signer owner must be canonical")
    try:
        canonical = address(value)
    except Exception:
        raise ValueError("signer owner must be canonical") from None
    if value != canonical:
        raise ValueError("signer owner must be canonical")
    return canonical


def _validate_pinned_configuration(configuration: dict) -> None:
    """Allow the request to vary only the canonical wallet owner."""

    pinned = _read_pinned_configuration()
    try:
        signer_process.validate_configuration(configuration)
        pinned_owner = _canonical_owner(pinned["scope"]["owner"])
        request_owner = _canonical_owner(configuration["scope"]["owner"])
    except Exception:
        raise ValueError("invalid signer configuration") from None

    if pinned_owner != pinned["scope"]["owner"]:
        raise ValueError("pinned signer owner is not canonical")
    expected = json.loads(json.dumps(pinned))
    expected["scope"]["owner"] = request_owner
    if configuration != expected:
        raise ValueError("signer configuration differs from pinned scope")


def _require_signer_identity() -> None:
    """Require the Linux launcher to run as the dedicated non-root user."""

    if os.name != "posix" or not hasattr(os, "geteuid"):
        raise RuntimeError("dedicated signer identity unavailable")
    uid = os.geteuid()
    if uid == ROOT_UID:
        raise RuntimeError("dedicated signer must not run as root")
    try:
        import pwd

        username = pwd.getpwuid(uid).pw_name
    except (KeyError, ImportError, OSError):
        raise RuntimeError("dedicated signer identity unavailable") from None
    if username != SIGNER_USER:
        raise RuntimeError("dedicated signer identity unavailable")


class HostedKmsSignerBridge(KmsSignerBridge):
    """Use the fixed OS boundary while reusing the canonical signer bridge."""

    def __init__(self, configuration, *, development_loopback: bool = False):
        self.development_loopback = development_loopback
        super().__init__(configuration)

    def _start(self):
        if self._broken is not None:
            raise RuntimeError("signer channel unavailable; inspect existing payment")
        if self.process is not None:
            return
        if sys.platform == "darwin":
            if not self.development_loopback:
                raise RuntimeError("loopback development opt-in required")
            return super()._start()
        if sys.platform != "linux":
            raise RuntimeError("hosted signer requires Linux OS boundary")
        _validate_linux_launcher()
        self.process = subprocess.Popen(
            [
                "/usr/bin/sudo",
                "-n",
                "-u",
                SIGNER_USER,
                "--",
                str(LINUX_LAUNCHER_PATH),
            ],
            cwd="/",
            env=_operator_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(
                json.dumps(self.bootstrap, separators=(",", ":")) + "\n"
            )
            self.process.stdin.flush()
        except Exception:
            self._mark_broken("signer channel unavailable")
            raise RuntimeError("signer channel unavailable") from None


class _PrefixReadline:
    """Replay one already-read line before delegating to the real stream."""

    def __init__(self, prefix: str, stream: TextIO):
        self._prefix: str | None = prefix
        self._stream = stream

    def readline(self, size: int = -1) -> str:
        if self._prefix is None:
            return self._stream.readline(size)
        prefix, self._prefix = self._prefix, None
        if size >= 0 and len(prefix) > size:
            self._prefix = prefix[size:]
            return prefix[:size]
        return prefix


def _read_bootstrap(stream: TextIO) -> tuple[str, dict]:
    line = stream.readline(MAX_LINE + 1)
    if not line or len(line) > MAX_LINE or not line.endswith("\n"):
        raise ValueError("invalid signer bootstrap framing")
    try:
        value = json.loads(line)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("invalid signer bootstrap") from None
    if not isinstance(value, dict):
        raise ValueError("invalid signer bootstrap")
    return line, value


def _startup_failure() -> int:
    print(
        json.dumps(
            {
                "id": None,
                "ok": False,
                "error": {
                    "message": "KMS signer unavailable; verify account, permission and key pins"
                },
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 2


def main() -> int:
    original_stdin = sys.stdin
    try:
        _require_signer_identity()
        line, configuration = _read_bootstrap(original_stdin)
        _validate_pinned_configuration(configuration)
        sys.stdin = _PrefixReadline(line, original_stdin)  # type: ignore[assignment]
        return signer_process.worker_main()
    except Exception:
        return _startup_failure()
    finally:
        sys.stdin = original_stdin


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "HostedKmsSignerBridge",
    "main",
]
