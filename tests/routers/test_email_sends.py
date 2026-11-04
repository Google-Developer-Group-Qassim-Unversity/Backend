"""Outbox contract, historical recipient shapes, and send write paths."""

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from fastapi import BackgroundTasks

from app.DB import email_jobs as jobs
from app.DB import emails
from app.DB.schema import (
    EmailJobs,
    EmailJobsType,
    EmailLogs,
    EmailLogsEmailType,
    EmailLogsFromAddress,
    EmailProvider,
    Events,
    EventsLocationType,
    EventsStatus,
    MembersGender,
)
from app.exceptions import BadGateway, GatewayTimeout, ServiceUnavailable
from app.routers import emails as routes
from app.routers.email_models import (
    BlastSendRequest,
    DirectEmailRequest,
    BlastGuaranteedRecipient,
    ManualCertificateMember,
    ManualCertificateRequest,
    CustomEmailRequest,
    SimpleEvent,
    SimpleMember,
    CertificateLanguage,
    CertificateFormat,
)
from app.services import email_jobs
from app.services.email_capacity import format_event_date
from tests.utils import semester_id_on

SEND_KEYS = {
    "id",
    "kind",
    "status",
    "subject",
    "event",
    "sender",
    "total",
    "succeeded",
    "failed",
    "created_at",
    "finished_at",
    "from_addresses",
}


def _job(session: Session, sender: int, kind: EmailJobsType = EmailJobsType.BLAST, **kwargs: Any) -> EmailJobs:
    job = jobs.create_job(session, kind, sender, total=2, subject="Subject", **kwargs)
    return job


def _log(
    session: Session,
    job: EmailJobs,
    data: dict[str, Any],
    *,
    email_type: EmailLogsEmailType = EmailLogsEmailType.BLAST,
    **kwargs: Any,
) -> EmailLogs:
    return emails.create_email_log(
        session,
        job_id=job.id,
        sent_by=job.created_by,
        from_address="sender@example.com",
        email_type=email_type,
        data=data,
        **kwargs,
    )


def test_job_timestamps_all_come_from_the_app_clock(db_session: Session, seed_refs: Any, monkeypatch: Any) -> None:
    """created_at used to be the database's UTC clock, 3h behind the rest of the row."""
    local = datetime(2026, 1, 2, 16, 34, 49)

    class AppClock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "AppClock":
            return cls(2026, 1, 2, 16, 34, 49)

    monkeypatch.setattr(jobs, "datetime", AppClock)
    job = _job(db_session, seed_refs.ahmed.id)
    jobs.mark_running(db_session, job.id)
    jobs.finish(db_session, job.id)
    db_session.refresh(job)
    assert (job.created_at, job.started_at, job.finished_at) == (local, local, local)


def test_page_groups_logs_excludes_team_and_uses_one_query(
    admin_client: TestClient, db_session: Session, seed_refs: Any
) -> None:
    job = _job(db_session, seed_refs.ahmed.id)
    _log(db_session, job, {}, recipient_count=1)
    _log(db_session, job, {}, recipient_count=2)
    _job(db_session, seed_refs.ahmed.id, EmailJobsType.TEAM_NOTIFICATION)
    db_session.commit()
    statements: list[str] = []

    def capture(connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        response = admin_client.get("/emails/sends")
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    reads = [statement for statement in statements if statement.lstrip().upper().startswith(("SELECT", "WITH"))]
    assert len(reads) == 1, statements
    body = response.json()
    assert set(body) == {"items", "next_before"}
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert set(item) == SEND_KEYS
    assert item["id"] == job.id
    assert item["kind"] == "campaign"
    assert item["sender"] == {"id": seed_refs.ahmed.id, "name": seed_refs.ahmed.name}
    assert item["from_addresses"] == [{"address": "sender@example.com", "count": 3}]
    assert body["next_before"] is None
    assert len(admin_client.get("/emails/sends?kind=team-notification").json()["items"]) == 1


def test_cursor_uses_dates_even_when_backfill_ids_are_newer(
    admin_client: TestClient, db_session: Session, seed_refs: Any
) -> None:
    newer = _job(db_session, seed_refs.ahmed.id)
    newer.created_at = datetime(2026, 10, 20)
    older = _job(db_session, seed_refs.ahmed.id)
    older.created_at = datetime(2026, 8, 20)
    same_day = _job(db_session, seed_refs.ahmed.id)
    same_day.created_at = newer.created_at
    db_session.commit()
    page = admin_client.get("/emails/sends?limit=1").json()
    assert page["items"][0]["id"] == same_day.id
    assert page["next_before"] == same_day.id
    page = admin_client.get(f"/emails/sends?limit=1&before={page['next_before']}").json()
    assert page["items"][0]["id"] == newer.id
    page = admin_client.get(f"/emails/sends?limit=1&before={page['next_before']}").json()
    assert page["items"][0]["id"] == older.id
    assert page["next_before"] is None


@pytest.mark.parametrize(
    "shape", ["recipients", "string-recipients", "member-list", "member", "recipient", "linked-member"]
)
def test_person_lookup_supports_all_historical_recipient_shapes(
    admin_client: TestClient, db_session: Session, seed_refs: Any, shape: str
) -> None:
    job = _job(db_session, seed_refs.ahmed.id)
    recipient = {"name": seed_refs.sara.name, "email": seed_refs.sara.email.upper()}
    data = {
        "recipients": {"recipients": [recipient]},
        "string-recipients": {"recipients": [recipient["email"]]},
        "member-list": {"member": [recipient]},
        "member": {"member": recipient},
        "recipient": {"recipient": recipient},
        "linked-member": {},
    }[shape]
    _log(db_session, job, data, member_id=seed_refs.sara.id if shape == "linked-member" else None)
    _job(db_session, seed_refs.ahmed.id)
    db_session.commit()
    for query in [f"member_id={seed_refs.sara.id}", f"email={seed_refs.sara.email}"]:
        response = admin_client.get(f"/emails/sends?{query}")
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["items"]] == [job.id]


def test_lookup_treats_email_wildcards_literally(admin_client: TestClient, db_session: Session, seed_refs: Any) -> None:
    job = _job(db_session, seed_refs.ahmed.id)
    _log(db_session, job, {"recipients": [{"email": "firstXlast@example.com"}]})
    db_session.commit()
    assert admin_client.get("/emails/sends?email=first_last@example.com").json()["items"] == []


def test_certificate_filter_preserves_the_two_response_kinds(
    admin_client: TestClient, db_session: Session, seed_refs: Any
) -> None:
    for kind in [EmailJobsType.EVENT_CERTIFICATE, EmailJobsType.MANUAL_CERTIFICATE, EmailJobsType.BLAST]:
        _job(db_session, seed_refs.ahmed.id, kind)
    db_session.commit()
    response = admin_client.get("/emails/sends?kind=certificate")
    assert {item["kind"] for item in response.json()["items"]} == {"event-certificate", "manual-certificate"}
    assert admin_client.get("/emails/sends?kind=blast").status_code == 422
    assert admin_client.get("/emails/sends?limit=0").status_code == 422
    assert admin_client.get("/emails/sends?limit=101").status_code == 422


def test_details_resolve_and_deduplicate_recipients_and_keep_message(
    admin_client: TestClient, db_session: Session, seed_refs: Any
) -> None:
    job = _job(db_session, seed_refs.ahmed.id)
    _log(
        db_session,
        job,
        {
            "recipients": [seed_refs.sara.email, "outside@example.com"],
            "subject": "Subject",
            "preview_text": "Preview",
            "html_content": "<p>Sent</p>",
            "attachments": [
                {"filename": "a.pdf", "url": "https://example.com/a.pdf"},
                {"filename": "legacy.pdf", "content_type": "application/pdf"},
            ],
        },
    )
    _log(db_session, job, {"recipients": [{"name": "Sara snapshot", "email": seed_refs.sara.email}]})
    jobs.record_failure(db_session, job.id, "Mailbox unavailable", ["failed@example.com"])
    response = admin_client.get(f"/emails/sends/{job.id}")
    assert response.status_code == 200, response.text
    item = response.json()
    assert set(item) == SEND_KEYS | {"recipients", "failures", "content"}
    assert item["recipients"] == [
        {"name": seed_refs.sara.name, "email": seed_refs.sara.email, "member_id": seed_refs.sara.id},
        {"name": None, "email": "outside@example.com", "member_id": None},
    ]
    assert item["failures"] == [{"recipient": "failed@example.com", "error": "Mailbox unavailable"}]
    assert item["content"] == {
        "subject": "Subject",
        "preview_text": "Preview",
        "html_content": "<p>Sent</p>",
        "attachments": [{"filename": "a.pdf", "url": "https://example.com/a.pdf"}],
    }
    assert admin_client.get("/emails/sends/999999999").status_code == 404


def test_certificate_details_have_no_content(admin_client: TestClient, db_session: Session, seed_refs: Any) -> None:
    job = _job(db_session, seed_refs.ahmed.id, EmailJobsType.EVENT_CERTIFICATE)
    emails.create_email_log(
        db_session,
        job_id=job.id,
        sent_by=job.created_by,
        from_address="sender@example.com",
        email_type=EmailLogsEmailType.EVENT_CERTIFICATE,
        member_id=seed_refs.sara.id,
        data={"member": {"name": "Sara", "email": seed_refs.sara.email}},
    )
    db_session.commit()
    response = admin_client.get(f"/emails/sends/{job.id}")
    assert response.json()["content"] is None
    assert response.json()["recipients"][0]["member_id"] == seed_refs.sara.id


def test_direct_write_path_keeps_subject_and_links_log(
    admin_client: TestClient, db_session: Session, seed_refs: Any, monkeypatch: pytest.MonkeyPatch, outbound: Any
) -> None:
    monkeypatch.setattr(routes, "get_from_address", lambda: EmailLogsFromAddress.GDG_QASSIM)
    tasks = BackgroundTasks()
    request = DirectEmailRequest(
        subject="Direct subject",
        html_content="<p>Hello</p>",
        recipients=[BlastGuaranteedRecipient(member_id=seed_refs.sara.id)],
    )
    response = routes.send_direct_email(request, seed_refs.ahmed, tasks, db_session)
    asyncio.run(tasks())
    db_session.expire_all()
    job_id = response["job_id"]
    job = db_session.get(EmailJobs, job_id)
    assert job is not None
    assert job.subject == "Direct subject"
    assert job.status.value == "succeeded", job.error
    log = db_session.scalar(select(EmailLogs).where(EmailLogs.job_id == job_id))
    assert log is not None
    assert log.member_id == seed_refs.sara.id
    assert admin_client.get(f"/emails/sends/{job_id}").json()["recipients"][0]["email"] == seed_refs.sara.email


def test_blast_chunk_failure_counts_and_records_every_address(
    db_session: Session, seed_refs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        email_jobs, "get_send_capacity", lambda address: 1 if address == EmailLogsFromAddress.GDG_QASSIM else 2
    )

    async def gateway(*args: Any, **kwargs: Any) -> None:
        if args[4] == EmailLogsFromAddress.INFO_KERNELTICS:
            raise RuntimeError("Chunk rejected")

    monkeypatch.setattr(email_jobs, "call_blast_api", gateway)
    job = jobs.create_job(db_session, EmailJobsType.BLAST, seed_refs.ahmed.id, total=3, subject="Blast")
    recipients = [{"email": f"person{i}@example.com", "name": None} for i in range(3)]
    request = BlastSendRequest(
        subject="Blast", html_content="<p>Hello</p>", count=3, order_by="activity", provider=EmailProvider.GOOGLE
    )
    asyncio.run(email_jobs.send_blast_job(recipients, [], 3, seed_refs.ahmed.id, request, job.id))
    db_session.refresh(job)
    assert (job.status.value, job.succeeded, job.failed) == ("partial", 1, 2)
    assert job.failures == [
        {"recipient": f"person{i}@example.com", "error": "RuntimeError: Chunk rejected"} for i in (1, 2)
    ]
    assert db_session.scalar(select(EmailLogs.job_id).where(EmailLogs.job_id == job.id)) == job.id


def test_failure_history_appends_and_caps_without_losing_counts(db_session: Session, seed_refs: Any) -> None:
    job = _job(db_session, seed_refs.ahmed.id)
    jobs.record_failure(db_session, job.id, "First", ["first@example.com"])
    jobs.record_failure(db_session, job.id, "Second", [f"person{i}@example.com" for i in range(501)])
    db_session.refresh(job)
    assert job.failed == 502
    assert job.error == "Second"
    assert job.failures is not None
    assert len(job.failures) == 500
    assert job.failures[0] == {"recipient": "first@example.com", "error": "First"}
    assert job.failures[-1]["error"] == "Second"


def test_outbox_requires_auth(client: TestClient) -> None:
    assert client.get("/emails/sends").status_code == 403
    assert client.get("/emails/sends/1").status_code == 403
    assert client.get("/emails/sends/1/certificate-sample").status_code == 403


@pytest.fixture
def certificate_gateway(monkeypatch: pytest.MonkeyPatch) -> Mock:
    gateway = Mock(return_value="https://example.com/certificate.png")
    monkeypatch.setattr(routes, "call_certificate_download", gateway)
    return gateway


@pytest.fixture
def certificate_event(db_session: Session) -> Events:
    event = Events(
        name="Current event name",
        location_type=EventsLocationType.ONLINE,
        location="Online",
        start_datetime=datetime(2026, 6, 29, 18),
        end_datetime=datetime(2026, 6, 30, 18),
        semester_id=semester_id_on(db_session, "2026-06-30"),
        status=EventsStatus.OPEN,
        is_official=0,
    )
    db_session.add(event)
    db_session.flush()
    return event


@pytest.mark.parametrize("snapshot", [True, False])
def test_event_certificate_sample(
    admin_client: TestClient,
    db_session: Session,
    seed_refs: Any,
    certificate_event: Events,
    certificate_gateway: Mock,
    snapshot: bool,
) -> None:
    job = _job(db_session, seed_refs.ahmed.id, EmailJobsType.EVENT_CERTIFICATE, event_id=certificate_event.id)
    sent_member = {"name": "Sent name", "email": "sent@example.com", "gender": "Male"}
    sent_event = {"name": "Sent event name", "date": "2026-05-20 - 2026-05-21", "official": True}
    _log(
        db_session,
        job,
        {"member": sent_member, "event": sent_event} if snapshot else {},
        email_type=EmailLogsEmailType.EVENT_CERTIFICATE,
        member_id=seed_refs.sara.id,
        event_id=certificate_event.id,
    )
    jobs.record_failure(db_session, job.id, "Rejected", ["failed@example.com"])
    db_session.commit()

    response = admin_client.get(f"/emails/sends/{job.id}/certificate-sample")
    assert response.status_code == 200, response.text
    certificate_gateway.assert_called_once()
    request = certificate_gateway.call_args.args[0]
    assert request.language == CertificateLanguage.ARABIC
    assert request.format == CertificateFormat.PNG
    expected_member = (
        sent_member
        if snapshot
        else {"name": seed_refs.sara.name, "email": seed_refs.sara.email, "gender": seed_refs.sara.gender.value}
    )
    expected_event = (
        sent_event
        if snapshot
        else {"name": certificate_event.name, "date": format_event_date(certificate_event), "official": False}
    )
    assert request.member.model_dump(mode="json") == expected_member
    assert request.event.model_dump(mode="json") == expected_event
    assert response.json() == {
        "recipient": {
            "name": expected_member["name"],
            "email": expected_member["email"],
            "member_id": seed_refs.sara.id,
        },
        "subject": f"شهادة حضور {expected_event['name']}",
        "event_name": expected_event["name"],
        "image_url": certificate_gateway.return_value,
    }


@pytest.mark.parametrize("linked_member", [True, False])
def test_manual_certificate_sample_uses_logged_ad_hoc_event(
    admin_client: TestClient, db_session: Session, seed_refs: Any, certificate_gateway: Mock, linked_member: bool
) -> None:
    job = _job(db_session, seed_refs.ahmed.id, EmailJobsType.MANUAL_CERTIFICATE)
    member_id = seed_refs.sara.id if linked_member else None
    member = {"name": "Manual name", "email": "manual@example.com", "gender": MembersGender.MALE.value}
    sent_event = {"name": "ورشة يدوية", "date": "2026-10-30", "official": True}
    _log(
        db_session,
        job,
        {"member": member, "event": sent_event},
        email_type=EmailLogsEmailType.MANUAL_CERTIFICATE,
        member_id=member_id,
    )
    db_session.commit()
    certificate_gateway.return_value = {"url": "https://example.com/manual.png"}

    response = admin_client.get(f"/emails/sends/{job.id}/certificate-sample")
    assert response.status_code == 200, response.text
    certificate_gateway.assert_called_once()
    request = certificate_gateway.call_args.args[0]
    assert request.model_dump(mode="json") == {"language": "ar", "format": "png", "member": member, "event": sent_event}
    assert response.json() == {
        "recipient": {"name": member["name"], "email": member["email"], "member_id": member_id},
        "subject": "شهادة حضور ورشة يدوية",
        "event_name": "ورشة يدوية",
        "image_url": "https://example.com/manual.png",
    }


@pytest.mark.parametrize("kind", [EmailJobsType.EVENT_CERTIFICATE, EmailJobsType.MANUAL_CERTIFICATE])
def test_certificate_sample_rejects_sends_without_successful_recipients(
    admin_client: TestClient, db_session: Session, seed_refs: Any, certificate_gateway: Mock, kind: EmailJobsType
) -> None:
    job = _job(db_session, seed_refs.ahmed.id, kind)
    jobs.record_failure(db_session, job.id, "Rejected", ["failed@example.com"])
    # Neither another send's successful log nor an unrelated message log qualifies.
    other = _job(db_session, seed_refs.ahmed.id, kind)
    _log(db_session, other, {}, email_type=EmailLogsEmailType(kind.value))
    _log(db_session, job, {})
    db_session.commit()
    response = admin_client.get(f"/emails/sends/{job.id}/certificate-sample")
    assert response.status_code == 422, response.text
    certificate_gateway.assert_not_called()


def test_certificate_sample_rejects_wrong_kind_and_missing_send(
    admin_client: TestClient, db_session: Session, seed_refs: Any, certificate_gateway: Mock
) -> None:
    job = _job(db_session, seed_refs.ahmed.id)
    db_session.commit()
    assert admin_client.get(f"/emails/sends/{job.id}/certificate-sample").status_code == 422
    assert admin_client.get("/emails/sends/999999999/certificate-sample").status_code == 404
    certificate_gateway.assert_not_called()


def test_certificate_sample_requires_logs_permission(clerk_client: TestClient, certificate_gateway: Mock) -> None:
    from app.main import app
    from app.services.permissions.dependencies import get_access
    from tests.access_doubles import StaffEverywhere

    app.dependency_overrides[get_access] = lambda: StaffEverywhere()
    try:
        response = clerk_client.get("/emails/sends/1/certificate-sample")
    finally:
        app.dependency_overrides.pop(get_access, None)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "permission_denied"
    assert "emails.logs" in response.json()["detail"]
    certificate_gateway.assert_not_called()


@pytest.mark.parametrize(
    "error_type, status_code", [(BadGateway, 502), (ServiceUnavailable, 503), (GatewayTimeout, 504)]
)
def test_certificate_sample_preserves_gateway_errors(
    admin_client: TestClient,
    db_session: Session,
    seed_refs: Any,
    certificate_gateway: Mock,
    error_type: Any,
    status_code: int,
) -> None:
    job = _job(db_session, seed_refs.ahmed.id, EmailJobsType.MANUAL_CERTIFICATE)
    _log(
        db_session,
        job,
        {
            "member": {"name": "Name", "email": "name@example.com", "gender": "Female"},
            "event": {"name": "Event", "date": "2026-10-30", "official": False},
        },
        email_type=EmailLogsEmailType.MANUAL_CERTIFICATE,
    )
    db_session.commit()
    certificate_gateway.side_effect = error_type(detail="Generation failed")
    response = admin_client.get(f"/emails/sends/{job.id}/certificate-sample")
    assert response.status_code == status_code, response.text
    assert response.json() == {"detail": "Generation failed"}


@pytest.mark.parametrize("kind", ["manual", "custom"])
def test_member_id_failures_record_resolved_email(
    db_session: Session, seed_refs: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    def rejected(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Rejected")

    async def rejected_async(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Rejected")

    monkeypatch.setattr(email_jobs, "call_certificate_api", rejected)
    monkeypatch.setattr(email_jobs, "call_custom_email_api", rejected_async)
    monkeypatch.setattr(email_jobs, "get_from_address", lambda: EmailLogsFromAddress.GDG_QASSIM)
    # Test sessions share one connection. A resolver snapshot keeps the working
    # session's savepoint from rolling back progress written by the tracker.
    resolved = SimpleMember(name=seed_refs.sara.name, email=seed_refs.sara.email, gender=seed_refs.sara.gender)
    resolved_id = seed_refs.sara.id
    monkeypatch.setattr(email_jobs, "_resolve_member", lambda item, session: (resolved, resolved_id))
    members = [ManualCertificateMember(member_id=resolved_id)]
    simple_event = SimpleEvent(name="Event", date="2026-10-30", official=False)
    job_type = EmailJobsType.MANUAL_CERTIFICATE if kind == "manual" else EmailJobsType.CUSTOM_EMAIL
    job = _job(db_session, seed_refs.ahmed.id, job_type)
    if kind == "manual":
        request = ManualCertificateRequest(event=simple_event, members=members, language=CertificateLanguage.ARABIC)
        email_jobs.send_manual_certificates_job(request, seed_refs.ahmed.id, job.id)
    else:
        custom = CustomEmailRequest(subject="Subject", html_content="<p>Hello</p>", members=members)
        asyncio.run(email_jobs.send_custom_email_job(custom, simple_event, 1, seed_refs.ahmed.id, job.id))
    db_session.refresh(job)
    assert job.failed == 1
    assert job.failures == [{"recipient": seed_refs.sara.email, "error": "RuntimeError: Rejected"}]
