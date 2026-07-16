"""Structured logging module for the web crawler.

Provides JSON-structured log output with timestamp, level, message, and
arbitrary context fields. Implements domain-specific logging methods for
state transitions, progress reporting, error context, worker lifecycle,
and rate limiter events.

Requirements: 18.1, 18.2, 18.3, 18.4, 18.5
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """Custom formatter that emits log records as single-line JSON objects.

    Each entry includes:
      - timestamp: ISO 8601 UTC
      - level: log level name (INFO, WARNING, ERROR, etc.)
      - message: human-readable log message
      - context: arbitrary structured fields attached via the `extra` dict
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }

        # Merge any context fields passed via extra={"context": {...}}
        context: dict[str, Any] = getattr(record, "context", {})
        if context:
            entry["context"] = context

        return json.dumps(entry, default=str)


class CrawlLogger:
    """Structured logger for the web crawler.

    Wraps Python's logging module with domain-specific methods that emit
    JSON-structured entries with relevant context fields.

    Attributes:
        progress_interval_ms: Minimum interval between progress log entries.
    """

    def __init__(
        self,
        name: str = "crawler",
        level: int = logging.INFO,
        progress_interval_ms: int = 10_000,
    ) -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        self._progress_interval_ms = progress_interval_ms
        self._last_progress_time_ms: float = 0.0

        # Avoid duplicate handlers when get_logger is called multiple times
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(JSONFormatter())
            self._logger.addHandler(handler)

    # ------------------------------------------------------------------
    # Generic log methods
    # ------------------------------------------------------------------

    def info(self, message: str, **context: Any) -> None:
        """Log an INFO-level message with optional context fields."""
        self._logger.info(message, extra={"context": context})

    def warn(self, message: str, **context: Any) -> None:
        """Log a WARNING-level message with optional context fields."""
        self._logger.warning(message, extra={"context": context})

    def debug(self, message: str, **context: Any) -> None:
        """Log a DEBUG-level message with optional context fields."""
        self._logger.debug(message, extra={"context": context})

    # ------------------------------------------------------------------
    # Requirement 18.1: State transition logging
    # ------------------------------------------------------------------

    def state_transition(
        self,
        url: str,
        from_state: str,
        to_state: str,
        **extra: Any,
    ) -> None:
        """Log a URL state change with structured fields.

        Args:
            url: The URL whose state changed.
            from_state: Previous crawl state.
            to_state: New crawl state.
            **extra: Additional context (e.g. worker_id, lease_token).
        """
        context: dict[str, Any] = {
            "url": url,
            "from_state": from_state,
            "to_state": to_state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        self._logger.info("state_transition", extra={"context": context})

    # ------------------------------------------------------------------
    # Requirement 18.2: Progress logging at configurable intervals
    # ------------------------------------------------------------------

    def progress(self, stats: dict[str, Any]) -> None:
        """Log crawl progress if the configured interval has elapsed.

        Expected stats keys: total_discovered, completed, failed,
        in_progress, queue_depth.

        Args:
            stats: Dictionary of progress counters.
        """
        now_ms = time.time() * 1000
        if now_ms - self._last_progress_time_ms < self._progress_interval_ms:
            return

        self._last_progress_time_ms = now_ms
        self._logger.info("progress", extra={"context": stats})

    def force_progress(self, stats: dict[str, Any]) -> None:
        """Log progress unconditionally, ignoring the interval gate."""
        self._last_progress_time_ms = time.time() * 1000
        self._logger.info("progress", extra={"context": stats})

    # ------------------------------------------------------------------
    # Requirement 18.3: Error logging with context
    # ------------------------------------------------------------------

    def error(
        self,
        url: str,
        error_type: str,
        error_message: str,
        retry_count: int | None = None,
        component: str | None = None,
    ) -> None:
        """Log an error with structured context.

        Args:
            url: The URL that triggered the error.
            error_type: Classification — "transient" or "permanent".
            error_message: Human-readable description.
            retry_count: Current retry attempt number (if applicable).
            component: Name of the component that raised the error.
        """
        context: dict[str, Any] = {
            "url": url,
            "error_type": error_type,
            "error_message": error_message,
        }
        if retry_count is not None:
            context["retry_count"] = retry_count
        if component is not None:
            context["component"] = component

        self._logger.error("error", extra={"context": context})

    # ------------------------------------------------------------------
    # Requirement 18.4: Worker lifecycle logging
    # ------------------------------------------------------------------

    def worker_event(
        self,
        worker_id: str,
        action: str,
        url: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Log a worker lifecycle event.

        Args:
            worker_id: Unique identifier of the worker.
            action: One of "start", "complete", "error".
            url: The URL being processed (if applicable).
            duration_ms: Processing duration in milliseconds.
        """
        context: dict[str, Any] = {
            "worker_id": worker_id,
            "action": action,
        }
        if url is not None:
            context["url"] = url
        if duration_ms is not None:
            context["duration_ms"] = duration_ms

        self._logger.info("worker_event", extra={"context": context})

    # ------------------------------------------------------------------
    # Requirement 18.5: Rate limiter event logging
    # ------------------------------------------------------------------

    def rate_limiter_event(
        self,
        event_type: str,
        duration_ms: float | None = None,
        queue_size: int | None = None,
    ) -> None:
        """Log a rate limiter event.

        Args:
            event_type: One of "backoff_start", "backoff_end",
                        "queue_overflow", "rate_limit_exhausted".
            duration_ms: Duration of the event in milliseconds.
            queue_size: Current queue depth at time of event.
        """
        context: dict[str, Any] = {
            "event_type": event_type,
        }
        if duration_ms is not None:
            context["duration_ms"] = duration_ms
        if queue_size is not None:
            context["queue_size"] = queue_size

        self._logger.info("rate_limiter_event", extra={"context": context})


# ------------------------------------------------------------------
# Module-level factory / singleton
# ------------------------------------------------------------------

_logger_instance: CrawlLogger | None = None


def get_logger(
    name: str = "crawler",
    level: int = logging.INFO,
    progress_interval_ms: int = 10_000,
) -> CrawlLogger:
    """Get or create a CrawlLogger singleton.

    Subsequent calls return the same instance (ignoring parameter changes).
    Use `reset_logger()` to force re-creation.

    Args:
        name: Logger name (default "crawler").
        level: Logging level (default INFO).
        progress_interval_ms: Minimum ms between progress logs.

    Returns:
        A configured CrawlLogger instance.
    """
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = CrawlLogger(
            name=name,
            level=level,
            progress_interval_ms=progress_interval_ms,
        )
    return _logger_instance


def reset_logger() -> None:
    """Reset the singleton logger (useful in tests)."""
    global _logger_instance
    _logger_instance = None
