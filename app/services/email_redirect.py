"""Where an email goes when this is not production.

Staging and local development run on copies of the production database, so
they hold every member's real address - and the pipeline sweep sends email on
its own, with nobody at the keyboard. Outside production, every send in
``app/services/email_gateway.py`` is redirected before it leaves:

- to **the member who clicked**: whoever sent the blast or the certificates
  gets every copy, so a developer testing a flow sees what it would have sent;
- to **``EMAIL_REDIRECT_FALLBACK``** when nobody clicked (the sweep), or the
  person who did has no address.

Each redirected email that has an HTML body opens with a box naming who it
would have gone to, the way the pipeline's trial copies already do.

Only ``ENV=staging`` and ``ENV=development`` redirect (``config.redirects_email``).
Production sends for real, and ``testing`` is left alone so the suite keeps
asserting on the real recipient lists.
"""

import html
import logging
from dataclasses import dataclass

from app.config import config
from app.DB.main import db_session
from app.DB.schema import Members
from app.exceptions import ServiceUnavailable

logger = logging.getLogger(__name__)

# A blast can be hundreds of addresses; the box shows this many and a count.
SHOWN_RECIPIENTS = 50


@dataclass(frozen=True)
class Redirect:
    to: list[str]
    would_have_sent_to: list[str]

    def banner(self) -> str:
        shown = self.would_have_sent_to[:SHOWN_RECIPIENTS]
        items = "".join(f"<li>{html.escape(email)}</li>" for email in shown)
        hidden = len(self.would_have_sent_to) - len(shown)
        if hidden:
            items += f"<li>and {hidden} more</li>"
        return (
            '<div style="border:2px dashed #b45309;padding:12px;margin-bottom:16px;font-family:sans-serif">'
            f"<strong>{html.escape(config.ENV.capitalize())} copy.</strong> "
            "In production this email goes to:"
            f"<ul>{items}</ul></div>"
        )

    def wrap(self, html_content: str) -> str:
        return self.banner() + html_content


def _clicker_email(on_behalf_of: int | None) -> str | None:
    if on_behalf_of is None:
        return None
    with db_session() as session:
        member = session.get(Members, on_behalf_of)
        return member.email if member else None


def redirect_for(recipients: list[str], on_behalf_of: int | None) -> Redirect | None:
    """The redirect for this send, or None when it should go out as addressed.

    ``on_behalf_of`` is the member whose click caused the send, None when
    nothing a person did caused it.
    """
    if not config.redirects_email:
        return None

    clicker = _clicker_email(on_behalf_of)
    to = [clicker] if clicker else config.EMAIL_REDIRECT_FALLBACK
    if not to:
        # Sending as addressed is the one thing this module exists to prevent.
        raise ServiceUnavailable(detail=f"{config.ENV} has nowhere to redirect this email: set EMAIL_REDIRECT_FALLBACK")
    logger.info("Redirecting an email for [%d] recipient(s) to %s", len(recipients), to)
    return Redirect(to=to, would_have_sent_to=recipients)
