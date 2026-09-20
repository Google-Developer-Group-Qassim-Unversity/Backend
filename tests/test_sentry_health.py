"""Cover the watcher that notices Sentry dropping our events.

The failure this guards against is silence: an over-quota Sentry returns 429,
the SDK discards the event, and nothing else changes. That looked exactly like
"no errors" for eight days in September 2026.
"""

import logging

from app.sentry_health import SentryDeliveryWatcher, delivery_status, install_delivery_watcher


def test_healthy_until_something_is_rejected():
    watcher = SentryDeliveryWatcher()
    assert watcher.status["ingesting"] is True
    assert watcher.status["rejections"] == 0
    assert watcher.status["last_rejection"] is None


def make_sdk_record(message: str) -> logging.LogRecord:
    return logging.LogRecord("sentry_sdk.errors", logging.WARNING, __file__, 1, message, None, None)


def test_rate_limit_flips_ingesting_to_false():
    watcher = SentryDeliveryWatcher()
    # the exact message the SDK emitted while the quota was exhausted
    watcher.emit(make_sdk_record("Rate-limited via x-sentry-rate-limits"))

    status = watcher.status
    assert status["ingesting"] is False
    assert status["rejections"] == 1
    assert "Rate-limited" in str(status["last_rejection"])
    assert status["last_rejection_at"] is not None


def test_unrelated_sdk_chatter_is_not_a_rejection():
    """The SDK logs plenty at WARNING that does not mean an event was lost."""
    watcher = SentryDeliveryWatcher()
    watcher.emit(make_sdk_record("Did not import default integration sentry_sdk.integrations.django"))
    assert watcher.status["ingesting"] is True


def test_rejections_accumulate():
    watcher = SentryDeliveryWatcher()
    for _ in range(3):
        watcher.emit(make_sdk_record("Rate-limited via x-sentry-rate-limits"))
    assert watcher.status["rejections"] == 3


def test_installing_twice_does_not_double_count():
    install_delivery_watcher()
    install_delivery_watcher()
    sdk_logger = logging.getLogger("sentry_sdk.errors")
    watchers = [h for h in sdk_logger.handlers if isinstance(h, SentryDeliveryWatcher)]
    assert len(watchers) == 1


def test_endpoint_reports_the_shared_watcher(client):
    response = client.get("/health/sentry")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"ingesting", "rejections", "last_rejection", "last_rejection_at"}
    assert body["ingesting"] == delivery_status()["ingesting"]
