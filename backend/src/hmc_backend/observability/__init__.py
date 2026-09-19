"""Sentry Tracing + structured Logs, safe no-ops when no DSN is configured."""

from hmc_backend.observability.sentry import (
    configure_sentry,
    flush_sentry,
    is_active,
    log_event,
    span,
    transaction,
)

__all__ = [
    "configure_sentry",
    "flush_sentry",
    "is_active",
    "log_event",
    "span",
    "transaction",
]
