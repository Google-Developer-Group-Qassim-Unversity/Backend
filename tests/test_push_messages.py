"""The pieces of push that need no database: text, time, links, the provider token, the APNs request, redaction."""

import asyncio
import json
import logging
from datetime import datetime

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from app.config import config, reload_settings
from app.services.push import apns, clock, delivery, links, messages, redact
from tests.push_support import KEY_ID, TEST_KEY

TOKEN = "ab" * 32


# ---------------------------------------------------------------- text


def test_reminder_text_gives_the_start_time_and_isolates_user_content():
    text = messages.reminder("Intro to ML", "حضوري، بيت الثقافة 📍", datetime(2026, 10, 13, 16, 30), 1, 1)
    assert text["ar"]["title"] == "تذكير: \u2068Intro to ML\u2069"
    assert text["ar"]["body"] == "تبدأ اليوم الساعة 4:30 م · \u2068حضوري، بيت الثقافة\u2069"
    assert text["en"]["body"] == "Starts today at 4:30 PM · \u2068حضوري، بيت الثقافة\u2069"


def test_reminder_text_for_a_later_day_of_a_multi_day_event():
    text = messages.reminder("Bootcamp", "none", datetime(2026, 10, 14, 10, 0), 2, 3)
    assert text["ar"]["body"] == "اليوم 2 من 3 يبدأ الساعة 10:00 ص"
    assert text["en"]["body"] == "Day 2 of 3 starts at 10:00 AM"


def test_accepted_and_new_event_texts_carry_the_date_as_the_app_writes_it():
    accepted = messages.accepted("Intro", datetime(2026, 10, 13, 10, 30))
    assert accepted["ar"] == {"title": "تم قبول تسجيلك", "body": "\u2068Intro\u2069 · 13 أكتوبر · 10:30 ص"}
    assert accepted["en"] == {"title": "Registration accepted", "body": "\u2068Intro\u2069 · Oct 13 · 10:30 AM"}
    new = messages.new_event("Intro", "Discord", datetime(2026, 10, 13, 0, 5))
    assert new["en"]["body"] == "Oct 13 · 12:05 AM · \u2068Discord\u2069"


@pytest.mark.parametrize(
    ("count", "noun"), [(1, "نقطة"), (2, "نقطتان"), (3, "نقاط"), (10, "نقاط"), (11, "نقطة"), (103, "نقاط")]
)
def test_points_title_uses_the_apps_plural_rule(count, noun):
    text = messages.points("Intro", count)
    assert text["ar"]["title"] == f"\u2066+{count}\u2069 {noun}"
    assert text["ar"]["body"] == "\u2068Intro\u2069 · أُضيفت إلى رصيدك"
    assert text["en"]["title"] == (f"+{count} point" if count == 1 else f"+{count} points")


def test_a_broadcast_without_english_sends_the_arabic_to_english_phones():
    text = messages.broadcast("عنوان", "نص", None, None)
    assert text["en"] == {"title": "عنوان", "body": "نص"}


# ---------------------------------------------------------------- time


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        (datetime(2026, 7, 20, 18, 59), datetime(2026, 7, 20, 18, 59)),  # 21:59 Riyadh: goes now
        (datetime(2026, 7, 20, 19, 0), datetime(2026, 7, 21, 5, 0)),  # 22:00 Riyadh: next 08:00
        (datetime(2026, 7, 20, 23, 30), datetime(2026, 7, 21, 5, 0)),  # 02:30 Riyadh: that morning's 08:00
        (datetime(2026, 7, 21, 5, 0), datetime(2026, 7, 21, 5, 0)),  # 08:00 Riyadh: goes now
    ],
)
def test_quiet_hours_are_22_to_08_in_riyadh(utc, expected):
    assert clock.after_quiet_hours(utc) == expected


def test_riyadh_day_bounds_are_utc_times():
    assert clock.riyadh_day_bounds(datetime(2026, 7, 20, 22, 0)) == (
        datetime(2026, 7, 20, 21, 0),
        datetime(2026, 7, 21, 21, 0),
    )


# ---------------------------------------------------------------- links


@pytest.mark.parametrize(
    "url",
    [
        "gdgq://events/435",
        "gdgq://points/history",
        "gdgq://notifications",
        "gdgq://profile/my-events",
        "gdgq://wallet/card",
    ],
)
def test_the_app_links_are_allowed(url):
    assert links.is_allowed(url)


@pytest.mark.parametrize(
    "url",
    ["https://gdg-q.com", "gdgq://events/", "gdgq://events/0", "gdgq://events/1/x", "gdgq://admin", "javascript:x"],
)
def test_anything_else_is_not(url):
    assert not links.is_allowed(url)


# ---------------------------------------------------------------- provider token


def test_provider_token_is_es256_with_the_key_id_and_team(push_on):
    token = apns.provider_token(now=1_800_000_123)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256" and header["kid"] == KEY_ID
    public = load_pem_private_key(TEST_KEY.encode(), password=None).public_key()
    claims = jwt.decode(token, public, algorithms=["ES256"], options={"verify_iat": False})
    assert claims == {"iss": config.APPLE_TEAM_ID, "iat": 1_800_000_123 // 3000 * 3000}


def test_provider_token_is_reused_within_its_50_minute_boundary(push_on):
    boundary = 1_800_000_000 // 3000 * 3000
    first = apns.provider_token(now=boundary + 10)
    assert apns.provider_token(now=boundary + 2999) == first
    assert apns.provider_token(now=boundary + 3000) != first


def test_a_forced_token_is_signed_now(push_on):
    token = apns.provider_token(now=1_800_000_123, force=True)
    assert jwt.decode(token, options={"verify_signature": False})["iat"] == 1_800_000_123


def test_a_missing_or_broken_key_names_the_setting_and_never_the_key(monkeypatch):
    monkeypatch.setenv("APNS_KEY_ID", KEY_ID)
    monkeypatch.setenv("APNS_PRIVATE_KEY", "not a key")
    reload_settings()
    apns.reset_provider_token()
    with pytest.raises(apns.ApnsNotConfigured) as caught:
        apns.provider_token()
    assert "not a key" not in str(caught.value)


def test_a_key_pasted_on_one_line_with_escaped_newlines_is_read(monkeypatch):
    monkeypatch.setenv("APNS_KEY_ID", KEY_ID)
    monkeypatch.setenv("APNS_PRIVATE_KEY", TEST_KEY.replace("\n", "\\n"))
    reload_settings()
    apns.reset_provider_token()
    assert apns.provider_token()
    apns.reset_provider_token()


def test_push_is_off_without_a_key_id_or_key(monkeypatch):
    monkeypatch.delenv("APNS_KEY_ID", raising=False)
    monkeypatch.setenv("APNS_PRIVATE_KEY", TEST_KEY)
    reload_settings()
    assert config.push_enabled is False


# ---------------------------------------------------------------- the request


def _send(handler, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    payload = kwargs.pop("payload", b"{}")
    return asyncio.run(
        apns.send(TOKEN, payload, priority=10, expiration=1_800_000_000, collapse_id="c" * 32, client=client, **kwargs)
    )


def test_a_request_carries_apples_headers(push_on):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    assert _send(handler) == apns.ApnsResponse(200)
    request = seen[0]
    assert str(request.url) == f"https://api.push.apple.com/3/device/{TOKEN}"
    assert request.headers["apns-topic"] == "com.gdgq.app"
    assert request.headers["apns-push-type"] == "alert"
    assert request.headers["apns-priority"] == "10"
    assert request.headers["apns-expiration"] == "1800000000"
    assert request.headers["apns-collapse-id"] == "c" * 32
    assert request.headers["authorization"].startswith("bearer ey")


def test_sandbox_goes_to_apples_sandbox_host(push_on, monkeypatch):
    monkeypatch.setenv("APNS_ENVIRONMENT", "sandbox")
    reload_settings()
    seen = []
    _send(lambda request: seen.append(request) or httpx.Response(200))
    assert seen[0].url.host == "api.sandbox.push.apple.com"


def test_an_expired_provider_token_is_rebuilt_once_and_the_device_tried_again(push_on):
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        if len(seen) == 1:
            return httpx.Response(403, json={"reason": "ExpiredProviderToken"})
        return httpx.Response(200)

    assert _send(handler).status == 200
    assert len(seen) == 2 and seen[0] != seen[1]


def test_a_second_expired_answer_is_not_retried_again(push_on):
    calls = []
    answer = _send(lambda r: calls.append(r) or httpx.Response(403, json={"reason": "ExpiredProviderToken"}))
    assert (answer.status, answer.reason, len(calls)) == (403, "ExpiredProviderToken", 2)


def test_410_carries_apples_timestamp(push_on):
    answer = _send(lambda r: httpx.Response(410, json={"reason": "Unregistered", "timestamp": 1_790_000_000_000}))
    assert answer == apns.ApnsResponse(410, "Unregistered", 1_790_000_000_000)


def test_no_answer_is_status_0(push_on):
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    assert _send(handler) == apns.ApnsResponse(0, "ConnectTimeout")


def test_a_payload_over_4kb_is_never_sent(push_on):
    calls = []
    answer = _send(lambda r: calls.append(r) or httpx.Response(200), payload=b"x" * 4097)
    assert answer.status == 413 and not calls


def test_the_payload_has_the_apps_keys_under_body_and_at_the_top():
    payload = json.loads(
        apns.device_payload(
            title="t", body="b", url="gdgq://events/1", kind="reminder", notification_id="id-1", thread_id="event-1"
        )
    )
    assert payload["aps"] == {"alert": {"title": "t", "body": "b"}, "sound": "default", "thread-id": "event-1"}
    assert payload["body"] == {"url": "gdgq://events/1", "kind": "reminder", "id": "id-1"}
    assert payload["url"] == "gdgq://events/1" and payload["kind"] == "reminder" and payload["id"] == "id-1"


@pytest.mark.parametrize(
    ("answer", "meaning"),
    [
        (apns.ApnsResponse(200), "sent"),
        (apns.ApnsResponse(410, "Unregistered"), "unregistered"),
        (apns.ApnsResponse(400, "BadDeviceToken"), "bad_token"),
        (apns.ApnsResponse(400, "DeviceTokenNotForTopic"), "bad_token"),
        (apns.ApnsResponse(403, "InvalidProviderToken"), "config"),
        (apns.ApnsResponse(403, "ExpiredProviderToken"), "config"),
        (apns.ApnsResponse(400, "TopicDisallowed"), "config"),
        (apns.ApnsResponse(-1, "APNS_KEY_ID is not set"), "config"),
        (apns.ApnsResponse(429, "TooManyProviderTokenUpdates"), "token_throttle"),
        (apns.ApnsResponse(429, "TooManyRequests"), "throttle"),
        (apns.ApnsResponse(503, "ServiceUnavailable"), "transient"),
        (apns.ApnsResponse(0, "ReadTimeout"), "transient"),
        (apns.ApnsResponse(413, "PayloadTooLarge"), "permanent"),
        (apns.ApnsResponse(400, "BadExpirationDate"), "permanent"),
    ],
)
def test_every_apns_answer_has_a_meaning(answer, meaning):
    assert delivery.classify(answer) == meaning


# ---------------------------------------------------------------- redaction


def test_the_httpx_request_line_never_shows_a_device_token():
    redact.install_log_filter()
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        'HTTP Request: %s %s "%s %d %s"',
        ("POST", httpx.URL(f"https://api.push.apple.com/3/device/{TOKEN}"), "HTTP/2", 200, "OK"),
        None,
    )
    for log_filter in logging.getLogger("httpx").filters:
        log_filter.filter(record)  # type: ignore[union-attr]
    message = record.getMessage()
    assert TOKEN not in message and "/3/device/[token]" in message


def test_sentry_breadcrumbs_and_spans_lose_the_token():
    crumb = {"type": "http", "data": {"url": f"https://api.push.apple.com/3/device/{TOKEN}", "method": "POST"}}
    assert TOKEN not in json.dumps(redact.before_breadcrumb(crumb))
    event = {"spans": [{"description": f"POST https://api.push.apple.com/3/device/{TOKEN}"}]}
    assert TOKEN not in json.dumps(redact.before_send_transaction(event))
