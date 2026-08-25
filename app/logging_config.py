"""Stdlib logging setup.

Output goes to stdout and nowhere else. In production the app runs under PM2
(`pm2 start ... --time`), which captures stdout into ~/.pm2/logs and stamps each
line with a timestamp - so the formatter here deliberately does not add one.

Four uvicorn workers share that one stream, so every line carries the PID and
the id of the request that produced it; without those, concurrent requests
interleave into something unreadable.
"""

import logging
import sys
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# no leading timestamp: `pm2 --time` already prefixes one
LOG_FORMAT = "%(levelname)s [pid:%(process)d] [req:%(request_id)s] %(name)s: %(message)s"


class RequestContextFilter(logging.Filter):
    """Stamp every record with the request it belongs to.

    A Filter rather than an adapter, so records from libraries that know nothing
    about this application - SQLAlchemy, httpx, uvicorn - are stamped too.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str | None = None) -> None:
    """Install the stdout handler on the root logger.

    Safe to call more than once; existing root handlers are replaced so a second
    call cannot double every line.
    """
    from app.config import config  # imported here: logging is configured before settings are needed

    resolved = (level or config.LOG_LEVEL).upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RequestContextFilter())

    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved)

    # uvicorn installs its own handlers; let them flow through this one instead
    # so access lines carry the same request id as everything else.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
