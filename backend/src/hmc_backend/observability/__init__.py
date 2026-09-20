"""Sentry Tracing + structured Logs, safe no-ops when no DSN is configured."""

from hmc_backend.observability.sentry import (
    capture_warning,
    configure_sentry,
    flush_sentry,
    is_active,
    log_event,
    span,
    transaction,
)

__all__ = [
    "capture_warning",
    "configure_sentry",
    "flush_sentry",
    "is_active",
    "log_event",
    "span",
    "transaction",
]
