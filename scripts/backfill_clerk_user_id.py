"""Backfill members.clerk_user_id for existing is_authenticated members.

Context: the non-qu-email signup branch adds a clerk_user_id column
(alembic revision f8771428c487) so members can be matched by Clerk identity
instead of just uni_id/email. Existing is_authenticated members signed up
before this column existed, so it needs a one-time backfill.

Every is_authenticated member so far signed up with uni_id/password, so their
Clerk primary email is the university-issued address derived from uni_id
(e.g. 442106350@qu.edu.sa) - NOT members.email, which holds their personal
email. Lookup is keyed on the derived uni email, not the email column.

Matching is done by fetching every Clerk user once and building an
email -> user id map locally, rather than trusting Clerk's email_address[]
query filter (found unreliable in manual testing).

Defaults to a dry run (prints the plan, no writes). Pass --apply to commit.
"""

import argparse
import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))
sys.path.insert(0, str(script_dir))

from sqlalchemy import select

from _clerk_backfill_common import build_email_to_user_ids, clerk_secret_key, derive_uni_email, fetch_all_clerk_users
from app.DB.main import SessionLocal
from app.DB.schema import Members


def main(apply: bool):
    secret_key = clerk_secret_key()

    print("Fetching all Clerk users...")
    users = fetch_all_clerk_users(secret_key)
    email_map = build_email_to_user_ids(users)
    print(f"Fetched {len(users)} Clerk users.")

    with SessionLocal() as session:
        members = session.scalars(
            select(Members).where(Members.is_authenticated == 1, Members.clerk_user_id.is_(None))
        ).all()

        planned: list[tuple[Members, str]] = []
        no_uni_id: list[Members] = []
        not_found: list[tuple[Members, str]] = []
        collisions: list[tuple[Members, str, list[str]]] = []

        for member in members:
            if not member.uni_id:
                no_uni_id.append(member)
                continue

            expected_email = derive_uni_email(member.uni_id)
            matches = email_map.get(expected_email, [])

            if len(matches) == 0:
                not_found.append((member, expected_email))
            elif len(matches) > 1:
                collisions.append((member, expected_email, matches))
            else:
                planned.append((member, matches[0]))

        print(f"\n--- plan: {len(planned)} member(s) to backfill ---")
        for member, clerk_id in planned:
            print(f"  member {member.id} (uni_id={member.uni_id}) -> {clerk_id}")

        if no_uni_id:
            print(f"\n--- skipped: {len(no_uni_id)} member(s) with no uni_id (unexpected for is_authenticated) ---")
            for member in no_uni_id:
                print(f"  member {member.id} email={member.email!r}")

        if not_found:
            print(f"\n--- not found in Clerk: {len(not_found)} member(s) ---")
            for member, expected_email in not_found:
                print(f"  member {member.id} (uni_id={member.uni_id}) expected {expected_email}")

        if collisions:
            print(f"\n--- COLLISIONS: {len(collisions)} member(s) whose expected email matches >1 Clerk user ---")
            for member, expected_email, clerk_ids in collisions:
                print(f"  member {member.id} (uni_id={member.uni_id}) {expected_email} -> {clerk_ids}")

        if apply:
            for member, clerk_id in planned:
                member.clerk_user_id = clerk_id
            session.commit()
            print(f"\nApplied: {len(planned)} member(s) updated.")
        else:
            print("\nDry run: no changes committed. Re-run with --apply to commit.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill members.clerk_user_id by matching derived uni email in Clerk"
    )
    parser.add_argument("--apply", action="store_true", help="Commit the backfill instead of just printing the plan")
    args = parser.parse_args()
    main(args.apply)
