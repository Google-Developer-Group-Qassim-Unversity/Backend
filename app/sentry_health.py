"""Notice when Sentry stops accepting what we send it.

A rate-limited Sentry fails silently by design: the SDK drops the event, the
request it belongs to still succeeds, and nothing in this application changes
behaviour. That is the worst possible failure mode for an error reporter - it
looks exactly like "no errors happened".

It has already cost this project a week. The organization's error quota ran out
on 2026-09-12; ingest answered every event with `429 error_usage_exceeded`
until it was noticed on 2026-09-20, by which time 1045 errors had been logged
on the VPS and none had reached Sentry.

The SDK does say so - it logs the rejection to its own `sentry_sdk.errors`
logger - it just says it somewhere nobody is looking. This module listens on
that logger and keeps the answer where a health check can reach it.
"""

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Substrings the SDK uses when it drops rather than delivers. Matched
# case-insensitively against its own log records.
_REJECTION_MARKERS = ("rate-limited", "rate limited", "dropping event", "discarding event")


class SentryDeliveryWatcher(logging.Handler):
    """Records the SDK's own complaints about undelivered events."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._lock = threading.Lock()
        self._rejections = 0
        self._last_rejection: str | None = None
        self._last_rejection_at: datetime | None = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # a broken record must never break logging
            return
        if not any(marker in message.lower() for marker in _REJECTION_MARKERS):
            return
        with self._lock:
            self._rejections += 1
            first = self._rejections == 1
            self._last_rejection = message
            self._last_rejection_at = datetime.now(timezone.utc)
        if first:
            # Say it once, loudly, in the place that still works - the pm2 log.
            # Sentry cannot report its own deafness.
            logger.error(
                "Sentry is not accepting events (%s). Errors from here on exist only in these logs "
                "until the quota or rate limit clears.",
                message,
            )

    @property
    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "ingesting": self._rejections == 0,
                "rejections": self._rejections,
                "last_rejection": self._last_rejection,
                "last_rejection_at": self._last_rejection_at.isoformat() if self._last_rejection_at else None,
            }


_watcher = SentryDeliveryWatcher()


def install_delivery_watcher() -> SentryDeliveryWatcher:
    """Attach the watcher to the SDK's logger. Safe to call more than once."""
    sdk_logger = logging.getLogger("sentry_sdk.errors")
    if _watcher not in sdk_logger.handlers:
        sdk_logger.addHandler(_watcher)
        # The SDK sets this False unless debug=True, which would stop the
        # records reaching any handler at all - including this one.
        sdk_logger.disabled = False
        sdk_logger.setLevel(min(sdk_logger.level or logging.WARNING, logging.WARNING))
    return _watcher


def delivery_status() -> dict[str, object]:
    return _watcher.status
