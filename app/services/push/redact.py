"""Keep APNs device tokens out of logs and Sentry.

A device token is the last segment of the APNs request path
(``/3/device/<token>``). httpx logs every request's URL at INFO, and Sentry's
httpx integration puts URLs into breadcrumbs and trace spans. Both are
rewritten here to ``/3/device/[token]``.

The Apple Wallet web service (``/wallet/apple/v1/devices/<id>/...``,
app/services/wallet_updates.py) has the same problem the other way round: the
device library id, which Apple treats as a shared secret, is in the path of
requests we receive, and a pass's ``pushToken`` is in their bodies. The
request log line, the error handler's and uvicorn's access line get
``/v1/devices/[device]``. In a Sentry event, a value under ``pushToken``,
``push_token`` or ``device_library_id`` is filtered anywhere, in dicts and in
the reprs of objects (``WalletPassRegistrationRequest(pushToken='...')``).

Two more places a token could reach: hpack's DEBUG lines print every HTTP/2
header (the path and the encoded ``authorization`` with the provider token),
so that logger stays at INFO even when ``LOG_LEVEL=DEBUG``; and a Sentry
event's stack frames carry each frame's local variables, so any local whose
name has ``token``, ``authorization``, ``device_library``, ``password``,
``secret``, ``p12``, ``pem`` or ``private_key`` in it is filtered (Sentry's own
list only matches the exact names; the last five keep the Pass Type ID
certificate, its key and their passwords out when loading it fails), and frames inside SQLAlchemy
and the MySQL driver lose their locals altogether: they hold every statement's
parameters, and the driver's the statement with its values written in. The
exception's own text never shows them either: the engine is built with
``hide_parameters`` (``app/DB/main.py``).
"""

import logging
import re
from typing import Any

_DEVICE_PATH = re.compile(r"/3/device/[0-9A-Fa-f]+")
_REPLACEMENT = "/3/device/[token]"
_PASS_DEVICE_PATH = re.compile(r"/v1/devices/[^/\s?#\"']+")
_PASS_DEVICE_REPLACEMENT = "/v1/devices/[device]"
_MARKERS = ("/3/device/", "/v1/devices/")
_FILTERED_KEYS = {"pushToken", "push_token", "device_library_id"}
# The Pass Type ID certificate's locals too (``p12_password``, ``p12_bytes``, ``pem``): Sentry's own
# list matches only exact names like ``password``, and a failed load would ship the key and its password.
_FILTERED_VARS = ("token", "authorization", "device_library", "password", "secret", "p12", "pem", "private_key")
_FILTERED = "[Filtered]"
# One of those keys in a repr: ``pushToken='...'``, ``'push_token': '...'``.
_KEYED_IN_TEXT = re.compile(r"""((?:pushToken|push_token|device_library_id)['"]?\s*[=:]\s*)(['"])[^'"]*\2""")
# Their frames' locals hold statements' parameters.
_DATABASE_MODULES = ("sqlalchemy.", "pymysql.")

# Loggers whose lines carry the path of a request we received.
_REQUEST_LOGGERS = ("app.middleware", "app.error_handlers", "uvicorn.access")


def _has_marker(value: str) -> bool:
    return any(marker in value for marker in _MARKERS)


def scrub(value: Any) -> Any:
    """``value`` with any device path rewritten and token or device id values filtered; inside dicts and lists too."""
    if isinstance(value, str):
        value = _PASS_DEVICE_PATH.sub(_PASS_DEVICE_REPLACEMENT, _DEVICE_PATH.sub(_REPLACEMENT, value))
        return _KEYED_IN_TEXT.sub(rf"\1\2{_FILTERED}\2", value)
    if isinstance(value, dict):
        return {key: _FILTERED if key in _FILTERED_KEYS else scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub(item) for item in value)
    return value


class HideDeviceTokens(logging.Filter):
    """On the ``httpx`` logger: its "HTTP Request: POST <url> ..." line keeps everything but the token.

    On the request loggers, a Wallet web service path keeps everything but the device library id.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(scrub(str(arg)) if _has_marker(str(arg)) else arg for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = scrub({key: str(arg) if _has_marker(str(arg)) else arg for key, arg in record.args.items()})
        if isinstance(record.msg, str) and _has_marker(record.msg):
            record.msg = scrub(record.msg)
        return True


def install_log_filter() -> None:
    for name in ("httpx", *_REQUEST_LOGGERS):
        target = logging.getLogger(name)
        if not any(isinstance(existing, HideDeviceTokens) for existing in target.filters):
            target.addFilter(HideDeviceTokens())
    hpack = logging.getLogger("hpack")
    if hpack.level < logging.INFO:  # NOTSET or DEBUG: it would follow LOG_LEVEL=DEBUG
        hpack.setLevel(logging.INFO)


def _filtered_var(name: Any) -> bool:
    lowered = str(name).lower()
    return any(part in lowered for part in _FILTERED_VARS)


def _database_frame(frame: dict) -> bool:
    return str(frame.get("module") or "").startswith(_DATABASE_MODULES)


def _filter_frame_vars(value: Any) -> Any:
    """Every stack frame's ``vars`` with token-named locals filtered, wherever frames sit in the event.

    A database library's frames lose their ``vars``.
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "vars" and isinstance(item, dict) and _database_frame(value):
                continue
            if key == "vars" and isinstance(item, dict):
                out[key] = {name: _FILTERED if _filtered_var(name) else var for name, var in item.items()}
            else:
                out[key] = _filter_frame_vars(item)
        return out
    if isinstance(value, list):
        return [_filter_frame_vars(item) for item in value]
    return value


def before_breadcrumb(crumb: Any, hint: Any = None) -> Any:
    return scrub(crumb)


def before_send_transaction(event: Any, hint: Any = None) -> Any:
    return scrub(event)


def before_send(event: Any, hint: Any = None) -> Any:
    return scrub(_filter_frame_vars(event))
