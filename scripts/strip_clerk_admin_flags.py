"""Remove the old admin flags from every Clerk user's public metadata.

`is_admin`, `is_admin_points` and `is_super_admin` decided access before the
permissions system (Notion: New permissions system). Nothing reads them now:
access comes from the roster, `super_admins` and the permission tables. This
removes only those three keys and leaves everything else in publicMetadata
(e.g. `onboardingComplete`, `uni_id`) alone.

Dry-run by default: it lists who has a flag and changes nothing until `--apply`.

    uv run python scripts/strip_clerk_admin_flags.py
    uv run python scripts/strip_clerk_admin_flags.py --apply

On the VPS, wrapped in Infisical for CLERK_SECRET_KEY:

    cd ~/GDG-backend && infisical run --env=prod --path=/admin-backend -- \\
        uv run python scripts/strip_clerk_admin_flags.py --apply

Clerk's metadata endpoint merges, and a key set to null is deleted.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import httpx

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))

from app.logging_config import configure_logging  # noqa: E402
from _clerk_backfill_common import (  # noqa: E402
    CLERK_API_BASE,
    PAGE_DELAY_SECONDS,
    clerk_secret_key,
    fetch_all_clerk_users,
)

logger = logging.getLogger(__name__)

FLAGS = ("is_admin", "is_admin_points", "is_super_admin")


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove the old admin flags from Clerk public metadata.")
    parser.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = parser.parse_args()

    configure_logging()
    secret = clerk_secret_key()
    users = fetch_all_clerk_users(secret)
    flagged = [u for u in users if any(k in (u.get("public_metadata") or {}) for k in FLAGS)]
    sys.stdout.write(f"{len(users)} Clerk users, {len(flagged)} with an old admin flag\n")
    for user in flagged:
        present = {k: user["public_metadata"][k] for k in FLAGS if k in user["public_metadata"]}
        sys.stdout.write(f"  {user['id']}: {present}\n")

    if not args.apply:
        sys.stdout.write("Dry run. Run again with --apply to remove them.\n")
        return 0

    failed = 0
    with httpx.Client(base_url=CLERK_API_BASE, headers={"Authorization": f"Bearer {secret}"}, timeout=30) as client:
        for user in flagged:
            response = client.patch(
                f"/users/{user['id']}/metadata", json={"public_metadata": {key: None for key in FLAGS}}
            )
            if response.is_success:
                logger.info("Removed the old admin flags from Clerk user %s", user["id"])
            else:
                failed += 1
                logger.error("Could not update Clerk user %s: %s %s", user["id"], response.status_code, response.text)
            time.sleep(PAGE_DELAY_SECONDS)

    sys.stdout.write(f"Done: {len(flagged) - failed} updated, {failed} failed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
