"""Unit tests for the CrawlLogger module.

Validates JSON-structured output, state_transition logging, progress interval
gating, error context, worker lifecycle events, and rate limiter event logging.

Requirements: 18.1, 18.2, 18.3, 18.4, 18.5
"""

import io
import json
import logging
import time

import pytest

from src.crawler.logger import CrawlLogger, JSONFormatter, get_logger, reset_logger


@pytest.fixture()
def logger_with_buffer() -> tuple[CrawlLogger, io.StringIO]:
    """Create a CrawlLogger wired to a StringIO buffer for assertion."""
    reset_logger()
    logger = CrawlLogger(name="test-logger", progress_interval_ms=0)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())
    logger._logger.handlers = [handler]
    return logger, buf


def _parse_last_line(buf: io.StringIO) -> dict:
    """Parse the last non-empty JSON line from the buffer."""
    lines = buf.getvalue().strip().splitlines()
    assert lines, "Expected at least one log line"
    return json.loads(lines[-1])


class TestJSONFormatter:
    """Verify that log output is valid single-line JSON with required fields."""

    def test_output_is_valid_json(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger.info("hello world", key="value")
        parsed = _parse_last_line(buf)
        assert parsed["timestamp"]
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello world"
        assert parsed["context"]["key"] == "value"

    def test_timestamp_is_iso8601(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger.info("check ts")
        parsed = _parse_last_line(buf)
        # ISO 8601 timestamps contain 'T' and end with timezone info
        assert "T" in parsed["timestamp"]
        assert "+" in parsed["timestamp"] or "Z" in parsed["timestamp"]


class TestStateTransition:
    """Requirement 18.1: URL state transition logging."""

    def test_logs_all_required_fields(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger.state_transition(
            "https://example.com/page",
            "Pending",
            "In_Progress",
            worker_id="w-1",
        )
        parsed = _parse_last_line(buf)
        ctx = parsed["context"]
        assert ctx["url"] == "https://example.com/page"
        assert ctx["from_state"] == "Pending"
        assert ctx["to_state"] == "In_Progress"
        assert ctx["worker_id"] == "w-1"
        assert "timestamp" in ctx  # state_transition adds its own timestamp too

    def test_extra_fields_propagate(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger.state_transition(
            "https://example.com",
            "In_Progress",
            "Completed",
            lease_token="abc-123",
        )
        parsed = _parse_last_line(buf)
        assert parsed["context"]["lease_token"] == "abc-123"


class TestProgressLogging:
    """Requirement 18.2: Progress logging at configurable intervals."""

    def test_first_call_always_logs(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger._progress_interval_ms = 10_000
        logger._last_progress_time_ms = 0.0
        stats = {
            "total_discovered": 50,
            "completed": 20,
            "failed": 3,
            "in_progress": 5,
            "queue_depth": 22,
        }
        logger.progress(stats)
        parsed = _parse_last_line(buf)
        assert parsed["context"]["total_discovered"] == 50

    def test_second_call_within_interval_is_suppressed(self) -> None:
        reset_logger()
        logger = CrawlLogger(name="test-interval", progress_interval_ms=60_000)
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JSONFormatter())
        logger._logger.handlers = [handler]

        # First call
        logger.progress({"x": 1})
        first = buf.getvalue()
        assert first.strip()

        # Second call immediately after — should be suppressed
        buf.truncate(0)
        buf.seek(0)
        logger.progress({"x": 2})
        assert buf.getvalue().strip() == ""

    def test_force_progress_ignores_interval(self) -> None:
        reset_logger()
        logger = CrawlLogger(name="test-force", progress_interval_ms=999_999)
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JSONFormatter())
        logger._logger.handlers = [handler]

        # Even with huge interval, force_progress should emit
        logger._last_progress_time_ms = time.time() * 1000
        logger.force_progress({"forced": True})
        parsed = _parse_last_line(buf)
        assert parsed["context"]["forced"] is True


class TestErrorLogging:
    """Requirement 18.3: Error logging with context."""

    def test_logs_all_error_fields(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger.error(
            "https://example.com/fail",
            "transient",
            "connection timeout",
            retry_count=2,
            component="worker",
        )
        parsed = _parse_last_line(buf)
        assert parsed["level"] == "ERROR"
        ctx = parsed["context"]
        assert ctx["url"] == "https://example.com/fail"
        assert ctx["error_type"] == "transient"
        assert ctx["error_message"] == "connection timeout"
        assert ctx["retry_count"] == 2
        assert ctx["component"] == "worker"

    def test_optional_fields_omitted_when_none(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger.error("https://example.com", "permanent", "not found")
        parsed = _parse_last_line(buf)
        ctx = parsed["context"]
        assert "retry_count" not in ctx
        assert "component" not in ctx


class TestWorkerEvent:
    """Requirement 18.4: Worker lifecycle logging."""

    def test_logs_start_event(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger.worker_event("worker-1", "start", url="https://example.com")
        parsed = _parse_last_line(buf)
        ctx = parsed["context"]
        assert ctx["worker_id"] == "worker-1"
        assert ctx["action"] == "start"
        assert ctx["url"] == "https://example.com"

    def test_logs_complete_with_duration(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger.worker_event(
            "worker-2", "complete", url="https://example.com/done", duration_ms=1234.5
        )
        parsed = _parse_last_line(buf)
        ctx = parsed["context"]
        assert ctx["action"] == "complete"
        assert ctx["duration_ms"] == 1234.5

    def test_optional_fields_omitted(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger.worker_event("worker-3", "error")
        parsed = _parse_last_line(buf)
        ctx = parsed["context"]
        assert "url" not in ctx
        assert "duration_ms" not in ctx


class TestRateLimiterEvent:
    """Requirement 18.5: Rate limiter event logging."""

    def test_backoff_start(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger.rate_limiter_event("backoff_start", duration_ms=5000, queue_size=42)
        parsed = _parse_last_line(buf)
        ctx = parsed["context"]
        assert ctx["event_type"] == "backoff_start"
        assert ctx["duration_ms"] == 5000
        assert ctx["queue_size"] == 42

    def test_queue_overflow(
        self, logger_with_buffer: tuple[CrawlLogger, io.StringIO]
    ) -> None:
        logger, buf = logger_with_buffer
        logger.rate_limiter_event("queue_overflow", queue_size=1000)
        parsed = _parse_last_line(buf)
        ctx = parsed["context"]
        assert ctx["event_type"] == "queue_overflow"
        assert ctx["queue_size"] == 1000
        assert "duration_ms" not in ctx


class TestGetLogger:
    """Test the module-level singleton factory."""

    def test_returns_same_instance(self) -> None:
        reset_logger()
        a = get_logger()
        b = get_logger()
        assert a is b

    def test_reset_creates_new_instance(self) -> None:
        reset_logger()
        a = get_logger()
        reset_logger()
        b = get_logger()
        assert a is not b
