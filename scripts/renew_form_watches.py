"""Renew every Google Forms watch. Run daily from cron.

Google expires a Forms watch seven days after it is created or last renewed,
and an expired watch stops delivering notifications **silently** - no error,
no failed request, just a form that never syncs again. A form attached more
than a week before its event would quietly stop working, which is the failure
mode this exists to prevent. See app/services/form_watches.py.

Daily is deliberate: it leaves six days of slack, so a missed run, a reboot or
a deploy costs nothing.

    uv run python scripts/renew_form_watches.py

On the VPS, wrapped in Infisical so it gets the same secrets the app runs with:

    cd ~/GDG-backend && infisical run --env=prod --path=/admin-backend -- \
        uv run python scripts/renew_form_watches.py

Exit codes, so cron can tell the difference:

    0  every watch renewed or recreated
    1  the sweep aborted - the club credential is dead, re-mint it with
       scripts/setup_google_oauth.py
    2  the sweep ran but at least one individual form failed
"""

import json
import logging
import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))

from app.DB.main import db_session  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.services.form_watches import renew_form_watches  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> int:
    configure_logging()
    with db_session() as session:
        summary = renew_form_watches(session)

    # stdout so a cron MAILTO or a redirect captures the outcome; the detail
    # is already in the app log via the service.
    sys.stdout.write(json.dumps(summary, indent=2, default=str) + "\n")

    if summary["aborted_reason"]:
        return 1
    if summary["failed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
