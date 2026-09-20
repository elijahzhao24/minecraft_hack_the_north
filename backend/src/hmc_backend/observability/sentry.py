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
from dataclasses import dataclass
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
    send_default_pii: bool = False,
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
        "send_default_pii": send_default_pii,
    }
    # Structured logs are opt-in. The pinned SDK exposes ``enable_logs`` as a
    # stable option; retaining the TypeError fallback keeps telemetry optional
    # if an older environment launches the service.
    init_kwargs["enable_logs"] = True
    try:
        sentry_sdk.init(**init_kwargs)
    except TypeError:  # pragma: no cover - compatibility with an old local SDK
        init_kwargs.pop("enable_logs", None)
        sentry_sdk.init(**init_kwargs)
    _active = True
    return True


def is_active() -> bool:
    return _active


@dataclass
class TransactionHandle:
    """Small SDK-neutral handle used for attributes and message propagation."""

    _span: Any = None

    def set_data(self, key: str, value: Any) -> None:
        if self._span is not None:
            self._span.set_data(key, value)

    def propagation_headers(self) -> dict[str, str]:
        if self._span is None:
            return {}
        with contextlib.suppress(Exception):
            return dict(self._span.iter_headers())
        return {}


@dataclass
class SpanHandle:
    _span: Any = None

    def set_data(self, key: str, value: Any) -> None:
        if self._span is not None:
            self._span.set_data(key, value)


@contextlib.contextmanager
def transaction(
    name: str,
    op: str = "task",
    *,
    sentry_trace: str | None = None,
    baggage: str | None = None,
    **attributes: Any,
) -> Iterator[TransactionHandle]:
    """Start or continue a Sentry transaction for one message."""
    if not _active:
        yield TransactionHandle()
        return
    if sentry_trace:
        incoming = {"sentry-trace": sentry_trace}
        if baggage:
            incoming["baggage"] = baggage
        context = sentry_sdk.continue_trace(incoming, name=name, op=op)
        started = sentry_sdk.start_transaction(transaction=context)
    else:
        started = sentry_sdk.start_transaction(name=name, op=op)
    with started as txn:
        for key, value in attributes.items():
            txn.set_data(key, value)
        yield TransactionHandle(txn)


@contextlib.contextmanager
def span(op: str, description: str | None = None, **attributes: Any) -> Iterator[SpanHandle]:
    """Start a child span (no-op if inactive)."""
    if not _active:
        yield SpanHandle()
        return
    with sentry_sdk.start_span(op=op, description=description) as active_span:
        for key, value in attributes.items():
            active_span.set_data(key, value)
        yield SpanHandle(active_span)


def log_event(level: str, message: str, **attributes: Any) -> None:
    """Emit a structured log line to Sentry Logs and the stdlib logger.

    ``attributes`` are scalar (session_id, frame_id, body_part, ...). Bulk data
    must never be passed here.
    """
    std_level = getattr(logging, level.upper(), logging.INFO)
    record_attributes = {
        f"hmc_{key}": value
        for key, value in attributes.items()
        if value is None or isinstance(value, (str, int, float, bool))
    }
    logger.log(std_level, message, extra=record_attributes)

    if not _active:
        return
    # With enable_logs=True the SDK's stdlib integration forwards this record
    # asynchronously. Custom LogRecord fields remain searchable attributes.


def capture_warning(message: str, **attributes: Any) -> None:
    """Emit one structured warning issue; callers own streak/rate limiting."""
    log_event("warning", message, **attributes)
    if not _active:
        return
    with contextlib.suppress(Exception), sentry_sdk.isolation_scope() as scope:
        for key, value in attributes.items():
            if value is None or isinstance(value, (str, int, float, bool)):
                scope.set_extra(key, value)
        scope.set_tag("hmc.warning", message)
        sentry_sdk.capture_message(message, level="warning")


def flush_sentry(timeout_s: float = 2.0) -> None:
    """Flush pending events on shutdown (no-op if inactive)."""
    if _active and _HAVE_SDK:
        with contextlib.suppress(Exception):
            sentry_sdk.flush(timeout=timeout_s)
