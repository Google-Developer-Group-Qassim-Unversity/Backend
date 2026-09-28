"""Notifications and department emails, including trial mode."""

import asyncio

import pytest
from sqlalchemy import select

from app.DB.schema import EmailJobs, EmailJobsStatus
from tests.pipeline_support import book_complete, submit


@pytest.fixture
def world(pipeline):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    pipeline.teams(design, logistics, media)
    ai = pipeline.department("AI")
    designers = [pipeline.officer(design, name="Designer"), pipeline.join(pipeline.person("Designer"), design)]
    logistics_team = [pipeline.officer(logistics, name="Logistics")]
    return {
        "ai": ai,
        "ai_leader": pipeline.officer(ai),
        "design": design,
        "designers": designers,
        "logistics": logistics,
        "logistics_team": logistics_team,
        "admin": pipeline.person("Admin", email="admin@example.com"),
    }


def test_trial_mode_emails_only_whoever_submitted(pipeline, world):
    pipeline.sign_in(world["admin"], super_admin=True)
    request_id = book_complete(pipeline, world["ai"])
    assert submit(pipeline, request_id).status_code == 200

    blasts = pipeline.outbound.to("/blasts")
    assert len(blasts) == 2  # one for Design, one for Logistics
    for blast in blasts:
        assert blast.params.get_list("emails") == ["admin@example.com"]
        assert blast.params["subject"].startswith("[تجربة · Trial]")
    design_blast = next(b for b in blasts if world["designers"][0].email in b.text)
    for designer in world["designers"]:
        assert designer.email in design_blast.text  # listed as who would have received it

    jobs = pipeline.session.scalars(select(EmailJobs).where(EmailJobs.created_by == world["admin"].id)).all()
    assert [j.total for j in jobs] == [1, 1]


def test_the_email_job_records_its_outcome(pipeline, world):
    from app.DB import email_jobs as job_queries
    from app.DB.schema import EmailJobsType
    from app.services.pipeline_notifications import PendingEmail, send_pipeline_email_job

    job = job_queries.create_job(pipeline.session, EmailJobsType.BLAST, world["admin"].id, total=1)
    pending = PendingEmail(emails=["a@example.com"], subject="s", html="<p>x</p>", sent_by_id=world["admin"].id)
    asyncio.run(send_pipeline_email_job(pending, job.id))

    pipeline.session.expire_all()
    finished = job_queries.get_job(pipeline.session, job.id)
    assert finished is not None and finished.status == EmailJobsStatus.SUCCEEDED
    assert pipeline.outbound.one("/blasts").params.get_list("emails") == ["a@example.com"]


def test_live_mode_emails_every_member_of_the_department(pipeline, world, monkeypatch):
    monkeypatch.setenv("PIPELINE_EMAILS_LIVE", "true")
    from app.config import reload_settings

    reload_settings()
    pipeline.sign_in(world["ai_leader"])
    submit(pipeline, book_complete(pipeline, world["ai"]))

    recipients = sorted(sorted(b.params.get_list("emails")) for b in pipeline.outbound.to("/blasts"))
    assert recipients == sorted([sorted(m.email for m in world["designers"]), [world["logistics_team"][0].email]])
    assert not any(b.params["subject"].startswith("[") for b in pipeline.outbound.to("/blasts"))


def test_a_failed_email_does_not_undo_the_submit(pipeline, world):
    pipeline.outbound.stub("/blasts", status_code=500)
    pipeline.sign_in(world["admin"], super_admin=True)
    request_id = book_complete(pipeline, world["ai"])
    with pytest.raises(Exception):
        submit(pipeline, request_id)  # the background job's failure surfaces in the test client
    assert pipeline.client.get(f"/pipeline/requests/{request_id}").json()["stage"] == "in_review"


def test_submit_notifies_design_and_logistics_only(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    submit(pipeline, book_complete(pipeline, world["ai"]))

    pipeline.sign_in(world["designers"][0])
    body = pipeline.client.get("/pipeline/notifications").json()
    assert body["unread"] == 1
    assert body["items"][0]["kind"] == "request_received"
    assert body["items"][0]["department"]["id"] == world["design"].id

    pipeline.sign_in(world["ai_leader"])
    assert pipeline.client.get("/pipeline/notifications").json()["total"] == 0


def test_the_receiving_team_can_open_the_request(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    request_id = book_complete(pipeline, world["ai"])
    pipeline.sign_in(world["designers"][0])
    assert pipeline.client.get(f"/pipeline/requests/{request_id}").status_code == 403

    pipeline.sign_in(world["ai_leader"])
    submit(pipeline, request_id)
    pipeline.sign_in(world["designers"][0])
    assert pipeline.client.get(f"/pipeline/requests/{request_id}").status_code == 200


def test_reads_are_per_person(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    submit(pipeline, book_complete(pipeline, world["ai"]))

    first, second = world["designers"]
    # The second designer is a plain member; their leader gives them access.
    pipeline.sign_in(first)
    pipeline.client.post(f"/departments/{world['design'].id}/permissions", json={"member_id": second.id})

    notification_id = pipeline.client.get("/pipeline/notifications").json()["items"][0]["id"]
    assert pipeline.client.post(f"/pipeline/notifications/{notification_id}/read").json()["count"] == 1
    assert pipeline.client.get("/pipeline/notifications").json()["unread"] == 0

    pipeline.sign_in(second)
    assert pipeline.client.get("/pipeline/notifications").json()["unread"] == 1
    assert pipeline.client.post("/pipeline/notifications/read-all").json()["count"] == 1


def test_a_ban_tells_the_requesting_team(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    request_id = book_complete(pipeline, world["ai"])
    pipeline.sign_in(world["logistics_team"][0])
    pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-21"], "reason": "Exams"})

    pipeline.sign_in(world["ai_leader"])
    item = pipeline.client.get("/pipeline/notifications").json()["items"][0]
    assert item["kind"] == "dates_banned"
    assert item["request"]["id"] == request_id
    assert item["payload"]["reason"] == "Exams"
