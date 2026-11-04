"""The deletion review must match database cascades, including cancelled rows."""

from datetime import datetime

from sqlalchemy import select

from app.DB.schema import Events, Forms, Logs, Members, MembersLogs, Submissions, EventContentLinks, EventHistory
from app.services.event_deletion import resource_filters
from tests.factories import make_create_event_payload
from tests.utils import assert_2xx, assert_not_found


def test_inventory_matches_rows_actually_deleted(super_admin_client, db_session, seed_refs):
    client = super_admin_client
    event = client.post("/events", json=make_create_event_payload(seed_refs=seed_refs)).json()
    other = client.post("/events", json=make_create_event_payload(seed_refs=seed_refs)).json()
    form = db_session.scalar(select(Forms).where(Forms.event_id == event["id"]))
    log = db_session.scalar(select(Logs).where(Logs.event_id == event["id"]).order_by(Logs.id))
    db_session.add_all(
        [
            Submissions(form_id=form.id, member_id=seed_refs.ahmed.id, submission_type="none"),
            Submissions(
                form_id=form.id, member_id=seed_refs.sara.id, submission_type="none", cancelled_at=datetime(2026, 7, 1)
            ),
            MembersLogs(log_id=log.id, member_id=seed_refs.ahmed.id, date=datetime(2026, 6, 29)),
            EventContentLinks(
                event_id=event["id"], label="Slides", url="https://example.com/slides", kind="slides", published=False
            ),
        ]
    )
    db_session.commit()
    preview = client.get(f"/events/{event['id']}/deletion-impact")
    assert_2xx(preview)
    body = preview.json()
    counts = {row["key"]: row["count"] for row in body["resources"]}
    assert counts["submissions"] == 2
    assert counts["members_logs"] == 1
    assert counts["event_content_links"] == 1
    assert body["total"] == 1 + sum(counts.values())
    assert set(counts) == set(resource_filters(event["id"])) - {"events"}
    rows = client.get(f"/events/{event['id']}/deletion-impact/submissions?page_size=1").json()
    assert rows["total"] == 2 and len(rows["items"]) == 1
    second = client.get(f"/events/{event['id']}/deletion-impact/submissions?page_size=1&page=2").json()
    assert rows["items"][0]["id"] != second["items"][0]["id"]
    assert {rows["items"][0]["label"], second["items"][0]["label"]} == {seed_refs.ahmed.name, seed_refs.sara.name}
    assert "cancelled_at" in second["items"][0]["details"]
    for resource in counts:
        assert_2xx(client.get(f"/events/{event['id']}/deletion-impact/{resource}"))
    assert_2xx(client.delete(f"/events/{event['id']}"))
    db_session.expire_all()
    # All reported descendants are gone, while other events and the audit trail stay.
    from app.DB.schema import Base
    from sqlalchemy import func

    for resource, where in resource_filters(event["id"]).items():
        assert db_session.scalar(select(func.count()).select_from(Base.metadata.tables[resource]).where(where)) == 0
    assert db_session.get(Events, other["id"]) is not None
    assert db_session.get(Members, seed_refs.ahmed.id) is not None
    assert db_session.scalar(select(EventHistory).where(EventHistory.event_id == event["id"])) is not None


def test_inventory_rejects_invalid_resources_and_pagination(super_admin_client, seed_refs):
    client = super_admin_client
    event = client.post("/events", json=make_create_event_payload(seed_refs=seed_refs)).json()
    base = f"/events/{event['id']}/deletion-impact"
    assert_not_found(client.get(base + "/members"))
    assert_not_found(client.get("/events/999999/deletion-impact"))
    assert client.get(base + "/submissions?page=0").status_code == 422
    assert client.get(base + "/submissions?page_size=101").status_code == 422
