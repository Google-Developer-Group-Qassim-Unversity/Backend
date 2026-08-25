"""Fold duplicate members.email rows into a single canonical member each.

Context: members.email is about to get a unique index (see alembic revision
e1f2a3b4c5d6), but some members have signed up twice under the same email
with a different uni_id (typo'd student id, duplicate registration, etc).
This script merges each such pair/group into one surviving row so the
unique index can actually be applied.

Canonical selection per duplicate-email group (in order):
  1. is_authenticated = 1 wins over 0
  2. most total activity (members_logs + submissions + email_logs rows) wins
  3. earliest created_at wins

Merge rules, non-canonical -> canonical:
  - role: moved only if canonical has no role row yet, otherwise the
    non-canonical role row is dropped (canonical's role wins)
  - members_logs: moved unless canonical already has a row for the same
    log_id, in which case the non-canonical row is dropped (dedupe to one
    credit instead of double-counting points for the same action)
  - submissions: moved unless canonical already has a row for the same
    form_id, in which case the non-canonical row is dropped
  - email_logs (member_id and sent_by): always reassigned to canonical
  - email_templates.created_by: always reassigned to canonical
  - the non-canonical member row is deleted once everything is moved

Defaults to a dry run (prints the plan, rolls back). Pass --apply to commit.
"""

import sys
import argparse
from collections import defaultdict
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))

from sqlalchemy import func, select
from app.DB.main import db_session
from app.DB.schema import EmailLogs, EmailTemplates, Members, MembersLogs, Role, Submissions


def find_duplicate_groups(session) -> list[list[Members]]:
    normalized_email = func.lower(func.trim(Members.email))
    dupe_emails = (
        session.execute(
            select(normalized_email)
            .where(Members.email.isnot(None), Members.email != "")
            .group_by(normalized_email)
            .having(func.count() > 1)
        )
        .scalars()
        .all()
    )

    groups = []
    for email in dupe_emails:
        members = session.scalars(select(Members).where(normalized_email == email).order_by(Members.id)).all()
        groups.append(list(members))
    return groups


def activity_count(session, member_id: int) -> int:
    counts = 0
    for model in (MembersLogs, Submissions, EmailLogs):
        counts += session.scalar(select(func.count()).select_from(model).where(model.member_id == member_id)) or 0
    return counts


def pick_canonical(session, members: list[Members]) -> Members:
    authenticated = [m for m in members if m.is_authenticated]
    if len(authenticated) == 1:
        return authenticated[0]

    pool = authenticated if authenticated else members
    scored = [(activity_count(session, m.id), m) for m in pool]
    max_score = max(score for score, _ in scored)
    top = [m for score, m in scored if score == max_score]
    if len(top) == 1:
        return top[0]

    return min(top, key=lambda m: m.created_at)


def merge_group(session, members: list[Members], report: list[str]) -> dict:
    canonical = pick_canonical(session, members)
    duplicates = [m for m in members if m.id != canonical.id]

    stats = {"role_moved": 0, "role_dropped": 0, "logs_moved": 0, "logs_deduped": 0, "subs_moved": 0, "subs_deduped": 0}

    report.append(
        f"\n=== {canonical.email} : keeping member {canonical.id} "
        f"({canonical.name!r}, uni_id={canonical.uni_id}, is_authenticated={canonical.is_authenticated}) ==="
    )

    for dup in duplicates:
        report.append(
            f"  folding member {dup.id} ({dup.name!r}, uni_id={dup.uni_id}, "
            f"is_authenticated={dup.is_authenticated}) into {canonical.id}"
        )

        canonical_role = session.scalar(select(Role).where(Role.member_id == canonical.id))
        for role_row in session.scalars(select(Role).where(Role.member_id == dup.id)).all():
            if canonical_role is None:
                role_row.member_id = canonical.id
                canonical_role = role_row
                stats["role_moved"] += 1
                report.append(f"    role {role_row.role}: moved (canonical had none)")
            else:
                report.append(f"    role {role_row.role}: dropped (canonical already has role {canonical_role.role})")
                session.delete(role_row)
                stats["role_dropped"] += 1

        canonical_log_ids = set(
            session.scalars(select(MembersLogs.log_id).where(MembersLogs.member_id == canonical.id)).all()
        )
        for log_row in session.scalars(select(MembersLogs).where(MembersLogs.member_id == dup.id)).all():
            if log_row.log_id in canonical_log_ids:
                report.append(f"    members_logs log_id={log_row.log_id} date={log_row.date}: deduped (dropped)")
                session.delete(log_row)
                stats["logs_deduped"] += 1
            else:
                log_row.member_id = canonical.id
                canonical_log_ids.add(log_row.log_id)
                stats["logs_moved"] += 1

        canonical_form_ids = set(
            session.scalars(select(Submissions.form_id).where(Submissions.member_id == canonical.id)).all()
        )
        for sub_row in session.scalars(select(Submissions).where(Submissions.member_id == dup.id)).all():
            if sub_row.form_id in canonical_form_ids:
                report.append(f"    submissions form_id={sub_row.form_id}: deduped (dropped)")
                session.delete(sub_row)
                stats["subs_deduped"] += 1
            else:
                sub_row.member_id = canonical.id
                canonical_form_ids.add(sub_row.form_id)
                stats["subs_moved"] += 1

        moved_recipient = session.execute(
            select(func.count()).select_from(EmailLogs).where(EmailLogs.member_id == dup.id)
        ).scalar()
        session.query(EmailLogs).filter(EmailLogs.member_id == dup.id).update({EmailLogs.member_id: canonical.id})
        moved_sender = session.execute(
            select(func.count()).select_from(EmailLogs).where(EmailLogs.sent_by == dup.id)
        ).scalar()
        session.query(EmailLogs).filter(EmailLogs.sent_by == dup.id).update({EmailLogs.sent_by: canonical.id})
        if moved_recipient or moved_sender:
            report.append(f"    email_logs: {moved_recipient} as recipient, {moved_sender} as sender reassigned")

        moved_templates = session.execute(
            select(func.count()).select_from(EmailTemplates).where(EmailTemplates.created_by == dup.id)
        ).scalar()
        session.query(EmailTemplates).filter(EmailTemplates.created_by == dup.id).update(
            {EmailTemplates.created_by: canonical.id}
        )
        if moved_templates:
            report.append(f"    email_templates: {moved_templates} reassigned")

        session.flush()
        session.delete(dup)

    return stats


def main(apply: bool):
    with db_session() as session:
        groups = find_duplicate_groups(session)
        report: list[str] = []
        totals = defaultdict(int)
        totals["groups"] = len(groups)

        for members in groups:
            stats = merge_group(session, members, report)
            for k, v in stats.items():
                totals[k] += v
            session.flush()

        print("\n".join(report))
        print("\n--- summary ---")
        for k, v in totals.items():
            print(f"{k}: {v}")

        if apply:
            session.commit()
            print("\nApplied.")
        else:
            session.rollback()
            print("\nDry run: no changes committed. Re-run with --apply to commit.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fold duplicate-email members into a single canonical row each")
    parser.add_argument("--apply", action="store_true", help="Commit the merge instead of just printing the plan")
    args = parser.parse_args()
    main(args.apply)
