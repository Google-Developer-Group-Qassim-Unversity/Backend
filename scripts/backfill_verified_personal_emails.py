"""Link each is_authenticated member's personal email to their Clerk account
as a verified secondary email.

Context: this is the historical-backfill half of the account-linking fix (the
onboarding flow now does this going forward for new signups - see
score-leaderboard-authentication/app/onboarding/_actions.ts,
addVerifiedPersonalEmail). Existing members signed up before that existed, so
if the real owner of their personal email later signs up with Google, Clerk
won't auto-link them into this account unless we add the email as a verified
secondary email first.

verified: true is a deliberate trust decision, accepted for prod: we vouch
for a self-typed, never-OTP'd email so Clerk's native account linking can
auto-merge a matching future Google signup. Requires clerk_user_id to already
be backfilled (run backfill_clerk_user_id.py first).

Before writing anything, each email is checked against every existing Clerk
user's email addresses. Google signups were never live in prod before this,
so a genuine collision (the email already belongs to a *different* Clerk
user) isn't expected - but any hit is logged and skipped rather than
overwritten, as a precaution.

Defaults to a dry run (prints the plan, no writes). Pass --apply to commit.
"""

import argparse
import sys
import time
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))
sys.path.insert(0, str(script_dir))

import httpx
from sqlalchemy import select

from _clerk_backfill_common import (
    CLERK_API_BASE,
    build_email_to_user_ids,
    clerk_secret_key,
    derive_uni_email,
    fetch_all_clerk_users,
)
from app.DB.main import db_session
from app.DB.schema import Members

CREATE_DELAY_SECONDS = 0.3


def main(apply: bool):
    secret_key = clerk_secret_key()

    print("Fetching all Clerk users...")
    users = fetch_all_clerk_users(secret_key)
    email_map = build_email_to_user_ids(users)
    print(f"Fetched {len(users)} Clerk users.")

    with db_session() as session:
        members = session.scalars(
            select(Members).where(
                Members.is_authenticated == 1,
                Members.clerk_user_id.isnot(None),
                Members.email.isnot(None),
                Members.email != "",
            )
        ).all()

        planned: list[tuple[Members, str]] = []
        no_personal_email: list[Members] = []
        already_linked: list[Members] = []
        collisions: list[tuple[Members, str, list[str]]] = []
        no_clerk_id_match: list[Members] = []

        clerk_users_by_id = {user["id"]: user for user in users}

        for member in members:
            personal_email = member.email.strip().lower()
            uni_email = derive_uni_email(member.uni_id) if member.uni_id else None

            if not personal_email or personal_email == uni_email:
                no_personal_email.append(member)
                continue

            clerk_user = clerk_users_by_id.get(member.clerk_user_id)
            if clerk_user is None:
                no_clerk_id_match.append(member)
                continue

            existing_emails = {e["email_address"].lower() for e in clerk_user.get("email_addresses", [])}
            if personal_email in existing_emails:
                already_linked.append(member)
                continue

            owners = email_map.get(personal_email, [])
            other_owners = [uid for uid in owners if uid != member.clerk_user_id]
            if other_owners:
                collisions.append((member, personal_email, other_owners))
                continue

            planned.append((member, personal_email))

        print(f"\n--- plan: {len(planned)} member(s) to get a verified secondary email ---")
        for member, email in planned:
            print(f"  member {member.id} (clerk_user_id={member.clerk_user_id}) -> {email}")

        if no_personal_email:
            print(f"\n--- skipped: {len(no_personal_email)} member(s) with no distinct personal email on file ---")

        if already_linked:
            print(f"\n--- skipped: {len(already_linked)} member(s) already linked in Clerk ---")
            for member in already_linked:
                print(f"  member {member.id} email={member.email!r}")

        if no_clerk_id_match:
            print(f"\n--- skipped: {len(no_clerk_id_match)} member(s) whose clerk_user_id wasn't found in Clerk ---")
            for member in no_clerk_id_match:
                print(f"  member {member.id} clerk_user_id={member.clerk_user_id!r}")

        if collisions:
            print(f"\n--- COLLISIONS (precautionary, not expected pre-Google-SSO): {len(collisions)} member(s) ---")
            for member, email, owners in collisions:
                print(f"  member {member.id} email={email} already claimed by Clerk user(s) {owners} - SKIPPED")

        if not apply:
            print("\nDry run: no changes made. Re-run with --apply to commit.")
            return

        headers = {"Authorization": f"Bearer {secret_key}"}
        succeeded: list[Members] = []
        failed: list[tuple[Members, str]] = []

        with httpx.Client(base_url=CLERK_API_BASE, headers=headers, timeout=30) as client:
            for member, email in planned:
                try:
                    resp = client.post(
                        "/email_addresses",
                        json={
                            "user_id": member.clerk_user_id,
                            "email_address": email,
                            "verified": True,
                            "primary": False,
                        },
                    )
                    resp.raise_for_status()
                    succeeded.append(member)
                except httpx.HTTPStatusError as exc:
                    failed.append((member, f"{exc.response.status_code}: {exc.response.text}"))
                time.sleep(CREATE_DELAY_SECONDS)

        print(f"\nApplied: {len(succeeded)} succeeded, {len(failed)} failed.")
        if failed:
            print("--- failures ---")
            for member, error in failed:
                print(f"  member {member.id} email={member.email!r}: {error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Add each is_authenticated member's personal email as a verified secondary Clerk email"
    )
    parser.add_argument("--apply", action="store_true", help="Commit the backfill instead of just printing the plan")
    args = parser.parse_args()
    main(args.apply)
