"""Tests that observability helpers are safe no-ops without a DSN."""

from __future__ import annotations

from hmc_backend.observability import (
    configure_console_logging,
    configure_sentry,
    flush_sentry,
    is_active,
    log_event,
    span,
    transaction,
)


def test_no_dsn_is_inactive():
    assert configure_sentry(None, environment="test", release="r", traces_sample_rate=1.0) is False
    assert is_active() is False


def test_helpers_noop_without_dsn():
    configure_sentry(None, environment="test", release="r", traces_sample_rate=1.0)
    # None of these should raise when Sentry is inactive.
    with transaction("t", capture_id="c"), span("op", "desc"):
        log_event("info", "hello", frame_id=1)
    flush_sentry()


def test_empty_string_dsn_is_inactive():
    assert configure_sentry("", environment="test", release="r", traces_sample_rate=1.0) is False


def test_console_logging_is_idempotent():
    configure_console_logging()
    configure_console_logging()

    from hmc_backend.observability.sentry import logger

    assert sum(handler.name == "hmc-console" for handler in logger.handlers) == 1
