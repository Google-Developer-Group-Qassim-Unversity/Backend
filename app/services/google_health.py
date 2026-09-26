"""Whether the one club Google credential still works, as a fact rather than an exception.

The refresh token in `app/services/google_client.py` is the single credential
every Drive/Forms call runs on, and it dies on a schedule: the OAuth client's
consent screen is in **Testing** publishing status, and Google expires refresh
tokens issued by a testing client after **seven days**. Not idle-expiry - a
timer that starts the moment the token is minted.

That is not a hypothetical either. It expired on 2026-09-20 at ~13:09 UTC and
nothing noticed for seven hours, because the only symptom was a
`RuntimeError: Caught handled exception, but response already started.` raised
by Starlette on the webhook path (the real `GoogleFormAuthExpired` was visible
only in `job_boundary`'s log line), and Sentry was over quota at the time. 11
form submissions sat unmatched while an event ran the next morning.

Same job as `app/sentry_health.py`: keep the answer somewhere an uptime check
can reach it, so a silent dependency failure becomes a page instead of an
archaeology exercise.

Deliberately *not* here: a "days until the token expires" countdown. Google
does not tell us when a refresh token was minted, so that number could only
come from a hand-maintained config value - which would go stale the first time
someone re-mints in a hurry and then quietly lie about a credential that is
already dead. `valid` is re-derived from Google on every check and cannot.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from google.auth.exceptions import RefreshError, TransportError

from app.services.google_client import get_google_credentials

logger = logging.getLogger(__name__)

# A success is cached this long so an uptime check polling every minute does
# not mint a new access token every minute. Failures are never cached: once
# the token is dead, every poll must see that immediately.
SUCCESS_TTL = timedelta(minutes=5)


class _CredentialHealth:
    """Last known answer, with a short time-to-live on the good one."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._valid_until: datetime | None = None
        self._first_failure_logged = False

    def _cached_ok(self, now: datetime) -> bool:
        return self._valid_until is not None and now < self._valid_until

    def check(self) -> dict[str, object]:
        now = datetime.now(timezone.utc)
        with self._lock:
            if self._cached_ok(now):
                return {"valid": True, "reason": None, "detail": None, "checked_at": now.isoformat(), "cached": True}

        reason, detail = _probe()
        now = datetime.now(timezone.utc)

        should_log = False
        with self._lock:
            if reason is None:
                self._valid_until = now + SUCCESS_TTL
                self._first_failure_logged = False
            else:
                self._valid_until = None
                should_log = not self._first_failure_logged
                self._first_failure_logged = True
            if should_log:
                # Say it once, loudly. Every Google Forms sync is down from
                # here until the token is re-minted, and nothing else in the
                # app will say so in a way that names the real cause.
                logger.error(
                    "Google credentials are not usable (%s: %s). Every Drive/Forms call will fail until "
                    "GOOGLE_REFRESH_TOKEN is re-minted - see scripts/setup_google_oauth.py.",
                    reason,
                    detail,
                )

        return {
            "valid": reason is None,
            "reason": reason,
            "detail": detail,
            "checked_at": now.isoformat(),
            "cached": False,
        }


def _probe() -> tuple[str | None, str | None]:
    """Ask Google. Returns `(reason, detail)`, both `None` when the token works."""
    try:
        get_google_credentials()
    except RefreshError as exc:
        message = str(exc)
        # Google answers a dead refresh token with `invalid_grant`, using the
        # same code for "expired" and "revoked" - it does not distinguish, so
        # neither can this.
        reason = "invalid_grant" if "invalid_grant" in message else "refresh_failed"
        return reason, message
    except TransportError as exc:
        # Google unreachable says nothing about whether the token is good.
        return "unreachable", str(exc)
    except Exception as exc:
        return "error", f"{type(exc).__name__}: {exc}"
    return None, None


_health = _CredentialHealth()


def credential_status() -> dict[str, object]:
    return _health.check()
