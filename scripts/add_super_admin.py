"""Make a member a super admin. For the first one on a database; after that, super admins add each other in the app.

Dry-run by default: it says who it found and changes nothing until ``--apply``.

    uv run python scripts/add_super_admin.py --uni-id 452106906
    uv run python scripts/add_super_admin.py --email someone@example.com --apply

On the VPS, wrapped in Infisical so it reaches the production database:

    cd ~/GDG-backend && infisical run --env=prod --path=/admin-backend -- \\
        uv run python scripts/add_super_admin.py --uni-id <id> --apply

Exit codes: 0 added (or would be, on a dry run) or already one; 1 no such member.
"""

import argparse
import logging
import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))

from app.DB import members as member_queries  # noqa: E402
from app.DB import permissions as permission_queries  # noqa: E402
from app.DB.main import db_session  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Make a member a super admin.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--uni-id")
    who.add_argument("--email")
    parser.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = parser.parse_args()

    configure_logging()
    with db_session() as session:
        if args.uni_id:
            member = member_queries.get_member_by_uni_id_or_none(session, args.uni_id)
        else:
            member = member_queries.get_member_by_email_or_none(session, args.email)
        if member is None:
            sys.stdout.write(f"No member with {'uni id ' + args.uni_id if args.uni_id else 'email ' + args.email}\n")
            return 1

        label = f"{member.name} (member {member.id}, uni id {member.uni_id}, {member.email})"
        if permission_queries.is_super_admin(session, member.id):
            sys.stdout.write(f"Already a super admin: {label}\n")
            return 0
        if not args.apply:
            sys.stdout.write(f"Would make a super admin: {label}\nRun again with --apply to do it.\n")
            return 0

        permission_queries.add_super_admin(session, member.id, added_by=None)
        session.commit()
        logger.info("Member %s is now a super admin (scripts/add_super_admin.py)", member.id)
        sys.stdout.write(f"Super admin added: {label}\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
