"""The member app's account routes under real, independent MySQL transactions.

Two requests that read the same member record before either has written it:
two sign-ins proving the same record, and an onboarding that overlaps the
account's own deletion. Each test commits its own rows and removes them.
"""

import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, or_, select, text
from sqlalchemy.orm import Session

from app.config import reload_settings
from app.DB.schema import MemberProfiles, Members, MembersGender
from app.exceptions import MemberLinkedToAnotherLogin, NotSignedIn
from app.routers.member_account_models import MemberOnboardingRequest
from app.services import member_accounts
from tests.routers.test_member_accounts import clerk_user


@pytest.fixture(autouse=True)
def clerk_secret(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_member_accounts")
    reload_settings()


@pytest.fixture
def committed_member(engine):
    """An admin-created record with a card, committed so that two connections can see it."""
    suffix = random.randint(10_000_000, 99_999_999)
    refs = SimpleNamespace(
        uni_id=f"7{suffix}",
        email=f"concurrent.{suffix}@gmail.com",
        qu_user=f"user_qu_{suffix}",
        google_user=f"user_google_{suffix}",
    )
    with Session(engine) as session:
        member = Members(
            name="عضو قديم", email=refs.email, uni_id=refs.uni_id, gender=MembersGender.MALE, is_authenticated=0
        )
        session.add(member)
        session.flush()
        session.add(
            MemberProfiles(
                member_id=member.id, uuid=f"concurrent{suffix}", created_at=datetime.now(), updated_at=datetime.now()
            )
        )
        session.commit()
        refs.member_id = member.id
    try:
        yield refs
    finally:
        with Session(engine) as session:
            ids = select(Members.id).where(
                or_(Members.id == refs.member_id, Members.clerk_user_id.in_([refs.qu_user, refs.google_user]))
            )
            member_ids = list(session.scalars(ids))
            session.execute(delete(MemberProfiles).where(MemberProfiles.member_id.in_(member_ids)))
            session.execute(delete(Members).where(Members.id.in_(member_ids)))
            session.commit()


def onboarding(status: str = "graduate", **fields) -> MemberOnboardingRequest:
    body = {"status": status, "name": "عبدالله محمد الأحمد", "gender": "Male", "personal_email": None} | fields
    return MemberOnboardingRequest.model_validate({k: v for k, v in body.items() if v is not None})


def run_in_threads(engine, *operations):
    """Each operation runs in its own thread with its own session, and commits or rolls back by itself."""

    def run(operation):
        with Session(engine) as session:
            session.execute(text("SET SESSION innodb_lock_wait_timeout = 10"))
            try:
                return operation(session)
            except (MemberLinkedToAnotherLogin, NotSignedIn) as exc:
                session.rollback()
                return exc

    with ThreadPoolExecutor(max_workers=len(operations)) as executor:
        futures = [executor.submit(run, operation) for operation in operations]
        return [future.result(timeout=30) for future in futures]


def test_two_sign_ins_proving_one_record_link_it_once(engine, committed_member, outbound, monkeypatch):
    """One proves the record's university ID, the other its email, and both read it before either writes."""
    refs = committed_member
    outbound.stub(
        f"/v1/users/{refs.qu_user}", json=clerk_user(refs.qu_user, (f"{refs.uni_id}@qu.edu.sa", "email_code"))
    )
    outbound.stub(f"/v1/users/{refs.google_user}", json=clerk_user(refs.google_user, (refs.email, "from_oauth_google")))

    both_looked = Barrier(2)
    find_member_for = member_accounts.find_member_for

    def find_then_wait(session, user):
        member = find_member_for(session, user)
        both_looked.wait(timeout=10)
        return member

    monkeypatch.setattr(member_accounts, "find_member_for", find_then_wait)

    def onboard(user_id, answers):
        def operation(session):
            member, _ = member_accounts.onboard(session, user_id, answers)
            return member.id

        return operation

    results = run_in_threads(
        engine,
        onboard(refs.qu_user, onboarding(personal_email="abdullah.m@gmail.com")),
        onboard(refs.google_user, onboarding()),
    )

    assert sum(isinstance(result, MemberLinkedToAnotherLogin) for result in results) == 1, results
    assert refs.member_id in results
    winner = refs.qu_user if results[0] == refs.member_id else refs.google_user
    with Session(engine) as session:
        row = session.get(Members, refs.member_id)
        assert row is not None and row.clerk_user_id == winner, "the record stays with the sign-in that linked it"
        assert len(outbound.to(f"/v1/users/{winner}/metadata")) == 1
        loser = refs.google_user if winner == refs.qu_user else refs.qu_user
        assert outbound.to(f"/v1/users/{loser}/metadata") == [], "the losing sign-in is not marked onboarded"


def test_onboarding_overlapping_the_accounts_deletion_writes_nothing_back(
    engine, committed_member, outbound, monkeypatch
):
    """The onboarding read the record before the deletion committed, and writes after it."""
    refs = committed_member
    outbound.stub(
        f"/v1/users/{refs.qu_user}", json=clerk_user(refs.qu_user, (f"{refs.uni_id}@qu.edu.sa", "email_code"))
    )
    with Session(engine) as session:
        session.execute(
            text("UPDATE members SET clerk_user_id = :sub, is_authenticated = 1 WHERE id = :id"),
            {"sub": refs.qu_user, "id": refs.member_id},
        )
        session.execute(delete(MemberProfiles).where(MemberProfiles.member_id == refs.member_id))
        session.commit()

    looked, deleted = Event(), Event()
    find_member_for = member_accounts.find_member_for

    def find_then_wait(session, user):
        member = find_member_for(session, user)
        assert member is not None and member.id == refs.member_id, "the onboarding finds the record by its sign-in"
        looked.set()
        assert deleted.wait(timeout=10)
        return member

    monkeypatch.setattr(member_accounts, "find_member_for", find_then_wait)

    def onboard(session):
        member, _ = member_accounts.onboard(session, refs.qu_user, onboarding(personal_email="abdullah.m@gmail.com"))
        return member.id

    def delete_account(session):
        assert looked.wait(timeout=10)
        try:
            return member_accounts.delete_account(session, refs.qu_user)
        finally:
            deleted.set()

    onboarded, anonymized = run_in_threads(engine, onboard, delete_account)

    assert anonymized is True
    assert isinstance(onboarded, NotSignedIn), onboarded
    with Session(engine) as session:
        row = session.get(Members, refs.member_id)
        assert row is not None and row.deleted_at is not None
        assert (row.name, row.email, row.phone_number, row.uni_id, row.clerk_user_id) == (
            "Deleted member",
            None,
            None,
            None,
            None,
        )
        assert session.scalar(select(MemberProfiles.id).where(MemberProfiles.member_id == refs.member_id)) is None
    assert outbound.to(f"/v1/users/{refs.qu_user}/metadata") == []
