"""Bounded diagnostics for hosted commerce operation failures.

Diagnostics are deliberately independent of operation inputs and exception
messages.  They are for local operator correlation only; they must never
change the business result or make sensitive request data observable.
"""

from __future__ import annotations

from functools import wraps
import logging
from os import path
from typing import Any, Callable, TypeVar


_MAX_TRACEBACK_FRAMES = 8

# These are the operation names emitted by the fixed hosted Marketplace and
# device dispatch surfaces.  Unknown caller-provided values are represented by
# ``outside_scope`` and are never copied into the log message.
SAFE_OPERATION_NAMES = frozenset({
    "status",
    "grant_challenge",
    "grant_verify",
    "budget_payload",
    "budget_bind",
    "approval_transaction",
    "verify_approval",
    "claim_transaction",
    "verify_claim",
    "revoke_prepare",
    "verify_revocation",
    "grant_renew_challenge",
    "grant_renew_verify",
    "feedback_prepare",
    "feedback_status",
    "feedback_verify",
    "assessment_record",
    "assessment_status",
    "service_history",
    "preview",
    "execute",
    "purchase",
    "search",
    "details",
    "recover_purchase",
    "initialize",
    "preview_state",
    "snapshot",
    "revoke",
    "onboarding_status",
    "onboarding",
    "hosted",
})

_F = TypeVar("_F", bound=Callable[..., Any])


def _safe_operation_name(operation: object) -> str:
    if isinstance(operation, str) and operation in SAFE_OPERATION_NAMES:
        return operation
    return "outside_scope"


def _basename(filename: object) -> str:
    if not isinstance(filename, str) or not filename:
        return "<unknown>"
    # ``os.path.basename`` is platform-specific.  Normalizing separators
    # first keeps a Windows-style absolute path from leaking on another host.
    return path.basename(filename.replace("\\", "/")) or "<unknown>"


def _traceback_summary(exc: BaseException) -> str:
    frames: list[str] = []
    traceback = exc.__traceback__
    while traceback is not None and len(frames) < _MAX_TRACEBACK_FRAMES:
        code = traceback.tb_frame.f_code
        frames.append(f"{_basename(code.co_filename)}:{code.co_name}:{traceback.tb_lineno}")
        traceback = traceback.tb_next
    return ",".join(frames) if frames else "none"


def log_operation_failure(operation: object, exc: BaseException) -> None:
    """Write only bounded, non-input diagnostics for one failed operation."""

    # Do not pass ``exc`` to logging: that would make exception text, args,
    # locals, and traceback formatting available to handlers.
    operation_name = _safe_operation_name(operation)
    exception_name = type(exc).__name__
    traceback_text = _traceback_summary(exc)
    logging.warning(
        "operation_failure operation=%s exception=%s traceback=%s",
        operation_name,
        exception_name,
        traceback_text,
    )


def capture_device_operation_failure(function: _F) -> _F:
    """Log a device operation failure and re-raise the identical exception."""

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        operation = kwargs.get("operation")
        if operation is None and len(args) >= 3:
            operation = args[2]
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            # Diagnostics are best-effort.  They must never replace the
            # business exception or cause a retry/second dispatch.
            try:
                log_operation_failure(operation, exc)
            except Exception:
                pass
            raise

    return wrapped  # type: ignore[return-value]


__all__ = [
    "SAFE_OPERATION_NAMES",
    "capture_device_operation_failure",
    "log_operation_failure",
]
