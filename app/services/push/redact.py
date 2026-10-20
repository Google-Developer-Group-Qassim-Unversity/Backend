"""Keep APNs device tokens out of logs and Sentry.

A device token is the last segment of the APNs request path
(``/3/device/<token>``). httpx logs every request's URL at INFO, and Sentry's
httpx integration puts URLs into breadcrumbs and trace spans. Both are
rewritten here to ``/3/device/[token]``.

Two more places a token could reach: hpack's DEBUG lines print every HTTP/2
header (the path and the encoded ``authorization`` with the provider token),
so that logger stays at INFO even when ``LOG_LEVEL=DEBUG``; and a Sentry
event's stack frames carry each frame's local variables, so any local whose
name has ``token`` in it is filtered (Sentry's own list only matches the
exact name ``token``).
"""

import logging
import re
from typing import Any

_DEVICE_PATH = re.compile(r"/3/device/[0-9A-Fa-f]+")
_REPLACEMENT = "/3/device/[token]"
_FILTERED = "[Filtered]"


def scrub(value: Any) -> Any:
    """``value`` with any device path rewritten; strings inside dicts and lists too."""
    if isinstance(value, str):
        return _DEVICE_PATH.sub(_REPLACEMENT, value)
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub(item) for item in value)
    return value


class HideDeviceTokens(logging.Filter):
    """On the ``httpx`` logger: its "HTTP Request: POST <url> ..." line keeps everything but the token."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(scrub(str(arg)) if "/3/device/" in str(arg) else arg for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = scrub(
                {key: str(arg) if "/3/device/" in str(arg) else arg for key, arg in record.args.items()}
            )
        if isinstance(record.msg, str) and "/3/device/" in record.msg:
            record.msg = scrub(record.msg)
        return True


def install_log_filter() -> None:
    target = logging.getLogger("httpx")
    if not any(isinstance(existing, HideDeviceTokens) for existing in target.filters):
        target.addFilter(HideDeviceTokens())
    hpack = logging.getLogger("hpack")
    if hpack.level < logging.INFO:  # NOTSET or DEBUG: it would follow LOG_LEVEL=DEBUG
        hpack.setLevel(logging.INFO)


def _filter_frame_vars(value: Any) -> Any:
    """Every stack frame's ``vars`` with token-named locals filtered, wherever frames sit in the event."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "vars" and isinstance(item, dict):
                out[key] = {name: _FILTERED if "token" in str(name).lower() else var for name, var in item.items()}
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
