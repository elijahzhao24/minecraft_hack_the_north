"""Thin Sentry wrapper: Tracing spans + structured Logs.

Initialized only when ``HMC_SENTRY_DSN`` is set; every helper degrades to a
harmless no-op otherwise, so the demo runs with cloud logging unavailable. Bulk
RGB/depth/point buffers are never attached — only scalar attributes and derived
measurements.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger("hmc")

_active = False

try:  # pragma: no cover - import guarded for environments without the SDK
    import sentry_sdk

    _HAVE_SDK = True
except ImportError:  # pragma: no cover
    sentry_sdk = None  # type: ignore[assignment]
    _HAVE_SDK = False


def configure_sentry(
    dsn: str | None,
    *,
    environment: str,
    release: str,
    traces_sample_rate: float,
) -> bool:
    """Initialize Sentry if a DSN is present; return whether it is active."""
    global _active
    if not dsn or not _HAVE_SDK:
        _active = False
        return False

    init_kwargs: dict[str, Any] = {
        "dsn": dsn,
        "environment": environment,
        "release": release,
        "traces_sample_rate": traces_sample_rate,
    }
    # Structured logs are opt-in on the SDK; enable when supported.
    with contextlib.suppress(TypeError):
        init_kwargs["_experiments"] = {"enable_logs": True}
    sentry_sdk.init(**init_kwargs)
    _active = True
    return True


def is_active() -> bool:
    return _active


@contextlib.contextmanager
def transaction(name: str, op: str = "task", **attributes: Any) -> Iterator[None]:
    """Start a Sentry transaction with scalar attributes (no-op if inactive)."""
    if not _active:
        yield
        return
    with sentry_sdk.start_transaction(name=name, op=op) as txn:
        for key, value in attributes.items():
            txn.set_tag(key, str(value))
        yield


@contextlib.contextmanager
def span(op: str, description: str | None = None) -> Iterator[None]:
    """Start a child span (no-op if inactive)."""
    if not _active:
        yield
        return
    with sentry_sdk.start_span(op=op, description=description):
        yield


def log_event(level: str, message: str, **attributes: Any) -> None:
    """Emit a structured log line to Sentry Logs and the stdlib logger.

    ``attributes`` are scalar (session_id, frame_id, body_part, ...). Bulk data
    must never be passed here.
    """
    std_level = getattr(logging, level.upper(), logging.INFO)
    logger.log(std_level, "%s %s", message, attributes)

    if not _active:
        return
    sentry_logger = getattr(sentry_sdk, "logger", None)
    if sentry_logger is not None:  # pragma: no cover - depends on SDK version
        emit = getattr(sentry_logger, level.lower(), None)
        if emit is not None:
            with contextlib.suppress(Exception):
                emit(message, **attributes)


def flush_sentry(timeout_s: float = 2.0) -> None:
    """Flush pending events on shutdown (no-op if inactive)."""
    if _active and _HAVE_SDK:
        with contextlib.suppress(Exception):
            sentry_sdk.flush(timeout=timeout_s)
