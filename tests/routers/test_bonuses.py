"""Tests for first-class /bonuses API, leaderboard aggregation, visibility, and snapshot immutability."""

from datetime import datetime

from sqlalchemy import select

from app.DB.schema import (
    Actions,
    ActionsActionType,
    Bonuses,
    Events,
    EventsLocationType,
    EventsStatus,
    Logs,
    Semesters,
    MembersLogs,
)
from tests.factories import make_create_event_payload
from tests.utils import assert_2xx, assert_forbidden, assert_not_found, semester_id_on


def test_standalone_bonus_crud_and_totals(admin_points_client, client, db_session, seed_refs):
    # Create a standalone bonus with both member and department grants in one atomic call.
    create_res = admin_points_client.post(
        "/bonuses",
        json={
            "title": "Coding Competition",
            "given_at": "2026-07-05T10:00:00",
            "is_public": True,
            "grants": [
                {
                    "recipient_type": "member",
                    "points": 12,
                    "label_en": "First Place",
                    "label_ar": "المركز الأول",
                    "member_ids": [seed_refs.ahmed.id, seed_refs.sara.id],
                },
                {
                    "recipient_type": "department",
                    "points": -4,
                    "label_en": "Late Report",
                    "label_ar": "تأخير التقرير",
                    "department_ids": [seed_refs.dept_business.id],
                },
            ],
        },
    )
    assert_2xx(create_res)
    bonus = create_res.json()
    assert bonus["event_id"] is None
    assert bonus["is_public"] is True
    assert len(bonus["grants"]) == 2

    # Never creates a fake event row.
    fake = db_session.scalars(select(Events).where(Events.name == "Coding Competition")).all()
    assert fake == []

    # Listed in GET /bonuses?standalone_only=true.
    listed = admin_points_client.get("/bonuses", params={"standalone_only": True, "semester": 475}).json()
    assert any(row["id"] == bonus["id"] and row["member_count"] == 2 and row["department_count"] == 1 for row in listed)

    # Reflected on public leaderboard totals & history.
    member_hist = client.get(f"/points/members/{seed_refs.ahmed.id}", params={"semester": 475}).json()
    assert member_hist["member"]["total_points"] == 12
    assert any(
        item["kind"] == "bonus"
        and item["bonus_id"] == bonus["id"]
        and item["event_id"] is None
        and item["label_ar"] == "المركز الأول"
        and item["points"] == 12
        for item in member_hist["history"]
    )

    dept_hist = client.get(f"/points/departments/{seed_refs.dept_business.id}", params={"semester": 475}).json()
    assert dept_hist["department"]["total_points"] == -4
    assert any(item["kind"] == "bonus" and item["points"] == -4 for item in dept_hist["history"])

    # Update bonus: change title, keep member grant in place (add only Sara -> verify Ahmed removed).
    member_grant_id = next(g["id"] for g in bonus["grants"] if g["recipient_type"] == "member")
    update_res = admin_points_client.put(
        f"/bonuses/{bonus['id']}",
        json={
            "title": "Coding Competition Final",
            "is_public": False,
            "grants": [
                {
                    "id": member_grant_id,
                    "recipient_type": "member",
                    "points": 15,
                    "label_en": "Winner",
                    "label_ar": "فائز",
                    "member_ids": [seed_refs.sara.id],
                }
            ],
        },
    )
    assert_2xx(update_res)
    updated = update_res.json()
    assert updated["title"] == "Coding Competition Final"
    assert updated["is_public"] is False
    assert len(updated["grants"]) == 1

    # Hidden bonus still counts in total_points, but is omitted from public history.
    sara_hist = client.get(f"/points/members/{seed_refs.sara.id}", params={"semester": 475}).json()
    assert sara_hist["member"]["total_points"] == 15
    assert not any(item["bonus_id"] == bonus["id"] for item in sara_hist["history"])

    # Delete bonus: points return to 0.
    assert_2xx(admin_points_client.delete(f"/bonuses/{bonus['id']}"))
    assert_not_found(admin_points_client.get(f"/bonuses/{bonus['id']}"))
    sara_after = client.get(f"/points/members/{seed_refs.sara.id}", params={"semester": 475}).json()
    assert sara_after["member"]["total_points"] == 0


def test_leaderboard_does_not_cartesian_multiply_events_and_bonuses(admin_points_client, client, db_session, seed_refs):
    """A member with multiple events AND multiple bonuses must get their exact sum, never a product."""
    sem_id = semester_id_on(db_session, "2026-07-05")
    for idx in range(3):
        ev = Events(
            name=f"Workshop {idx}",
            location_type=EventsLocationType.ON_SITE,
            location="Hall",
            start_datetime=f"2026-07-0{idx + 1} 10:00:00",
            end_datetime=f"2026-07-0{idx + 1} 12:00:00",
            status=EventsStatus.PUBLISHED,
            semester_id=sem_id,
        )
        db_session.add(ev)
        db_session.flush()
        log = Logs(action_id=seed_refs.member_action.id, event_id=ev.id)
        db_session.add(log)
        db_session.flush()
        db_session.add(MembersLogs(member_id=seed_refs.ahmed.id, log_id=log.id, date=datetime(2026, 7, idx + 1, 10, 0)))
    db_session.commit()

    for idx in range(2):
        assert_2xx(
            admin_points_client.post(
                "/bonuses",
                json={
                    "title": f"Bonus {idx}",
                    "given_at": f"2026-07-1{idx}T10:00:00",
                    "is_public": True,
                    "grants": [{"recipient_type": "member", "points": 5, "member_ids": [seed_refs.ahmed.id]}],
                },
            )
        )

    expected = 3 * seed_refs.member_action.points + 2 * 5
    totals = {
        r["member_id"]: r["total_points"] for r in client.get("/points/members/total", params={"semester": 475}).json()
    }
    assert totals[seed_refs.ahmed.id] == expected


def test_bonus_grant_snapshots_template_action_points(admin_points_client, client, db_session, seed_refs):
    """Editing a template action's points later must NOT rewrite existing bonus grants."""
    template = Actions(action_name="Speaker", ar_action_name="متحدث", action_type=ActionsActionType.BONUS, points=8)
    db_session.add(template)
    db_session.commit()

    res = admin_points_client.post(
        "/bonuses",
        json={
            "title": "Tech Talk",
            "given_at": "2026-07-05T10:00:00",
            "is_public": True,
            "grants": [
                {
                    "recipient_type": "member",
                    "points": 999,  # overridden by template's 8
                    "source_action_id": template.id,
                    "member_ids": [seed_refs.ahmed.id],
                }
            ],
        },
    )
    assert_2xx(res)
    bonus = res.json()
    assert bonus["grants"][0]["points"] == 8
    assert bonus["grants"][0]["label_ar"] == "متحدث"

    # Change the template action's value to 20.
    assert_2xx(admin_points_client.put(f"/actions/{template.id}", json={"points": 20}))
    hist = client.get(f"/points/members/{seed_refs.ahmed.id}", params={"semester": 475}).json()
    assert hist["member"]["total_points"] == 8

    assert_2xx(
        admin_points_client.put(
            f"/bonuses/{bonus['id']}",
            json={
                "grants": [
                    {
                        "id": bonus["grants"][0]["id"],
                        "recipient_type": "member",
                        "source_action_id": template.id,
                        "points": 8,
                        "member_ids": [seed_refs.ahmed.id, seed_refs.sara.id],
                    }
                ]
            },
        )
    )
    assert (
        client.get(f"/points/members/{seed_refs.sara.id}", params={"semester": 475}).json()["member"]["total_points"]
        == 8
    )


def test_event_attached_bonuses_upsert_and_visibility(admin_points_client, client, db_session, seed_refs):
    event = admin_points_client.post("/events", json=make_create_event_payload(seed_refs=seed_refs)).json()

    # Empty initially.
    initial = admin_points_client.get(f"/bonuses/by-event/{event['id']}").json()
    assert initial["bonus_id"] is None
    assert initial["grants"] == []

    # Upsert grants on the event.
    upserted = admin_points_client.put(
        f"/bonuses/by-event/{event['id']}",
        json={
            "grants": [
                {
                    "recipient_type": "member",
                    "points": 6,
                    "label_en": "Host",
                    "label_ar": "مقدم",
                    "member_ids": [seed_refs.ahmed.id],
                }
            ]
        },
    ).json()
    assert upserted["bonus_id"] is not None
    assert len(upserted["grants"]) == 1

    # Because the event is still draft, its attached bonus doesn't count yet.
    assert (
        client.get(f"/points/members/{seed_refs.ahmed.id}", params={"semester": 475}).json()["member"]["total_points"]
        == 0
    )

    # Publish the event -> attached bonus now counts and links to event_id.
    assert_2xx(admin_points_client.put(f"/events/{event['id']}/status", json={"status": "published"}))
    hist = client.get(f"/points/members/{seed_refs.ahmed.id}", params={"semester": 475}).json()
    assert hist["member"]["total_points"] == 6
    assert any(item["kind"] == "bonus" and item["event_id"] == event["id"] for item in hist["history"])

    # Explicitly filing the event in a different semester moves the attached grant too.
    target = db_session.scalar(select(Semesters).where(Semesters.is_public == 1, Semesters.hijri_code != 475))
    assert target is not None
    event_row = db_session.get(Events, event["id"])
    assert event_row is not None
    event_row.semester_id = target.id
    db_session.commit()
    path = f"/points/members/{seed_refs.ahmed.id}"
    assert client.get(path, params={"semester": 475}).json()["member"]["total_points"] == 0
    assert client.get(path, params={"semester": target.hijri_code}).json()["member"]["total_points"] == 6

    # Clearing grants removes the bonus container cleanly.
    cleared = admin_points_client.put(f"/bonuses/by-event/{event['id']}", json={"grants": []}).json()
    assert cleared["bonus_id"] is None
    assert cleared["grants"] == []
    assert db_session.scalars(select(Bonuses).where(Bonuses.event_id == event["id"])).all() == []


def test_bonuses_routes_require_points_custom_permission(clerk_client):
    assert_forbidden(clerk_client.get("/bonuses"))


def test_events_me_includes_public_standalone_bonuses(admin_points_client, clerk_client, db_session, seed_refs):
    """GET /events/me surfaces public standalone bonuses under `bonuses` and leaves hidden ones out."""
    me_id = clerk_client.get("/members/me").json()["id"]
    assert_2xx(
        admin_points_client.post(
            "/bonuses",
            json={
                "title": "Public Hackathon",
                "given_at": "2026-07-05T10:00:00",
                "is_public": True,
                "grants": [{"recipient_type": "member", "points": 10, "member_ids": [me_id]}],
            },
        )
    )
    assert_2xx(
        admin_points_client.post(
            "/bonuses",
            json={
                "title": "Secret Adjustment",
                "given_at": "2026-07-06T10:00:00",
                "is_public": False,
                "grants": [{"recipient_type": "member", "points": 5, "member_ids": [me_id]}],
            },
        )
    )
    res = clerk_client.get("/events/me")
    assert_2xx(res)
    body = res.json()
    assert [b["title"] for b in body["bonuses"]] == ["Public Hackathon"]
    assert body["bonuses"][0]["points"] == 10
    assert [p["name"] for p in body["participated"]] == ["Public Hackathon"]
    legacy = body["participated"][0]
    assert legacy["location_type"] == "none"
    assert legacy["id"] > 0
    history = clerk_client.get(f"/points/members/{me_id}", params={"semester": 475}).json()
    assert [row["event_id"] for row in history["events"]] == [legacy["id"]]


def test_legacy_ios_award_can_be_read_by_numeric_id(admin_points_client, seed_refs):
    created = admin_points_client.post(
        "/custom/members",
        json={
            "event_name": "Volunteer hours",
            "start_datetime": "2026-07-07T10:00:00",
            "end_datetime": "2026-07-08T10:00:00",
            "location_type": "none",
            "point_deatils": [{"points": 8, "member_ids": [seed_refs.ahmed.id]}],
        },
    )
    assert_2xx(created)
    event_id = created.json()["event_id"]
    assert isinstance(event_id, int) and event_id > 0
    read = admin_points_client.get(f"/custom/members/{event_id}")
    assert_2xx(read)
    assert read.json()["end_datetime"] == "2026-07-08T10:00:00"
    [row] = read.json()["point_details"]
    assert isinstance(row["log_id"], int)
    assert row["points"] == 8
    assert row["member_ids"] == [seed_refs.ahmed.id]


def test_deleting_action_used_by_bonus_grant_requires_replacement(admin_points_client, db_session, seed_refs):
    template = Actions(action_name="Judge", ar_action_name="محكم", action_type=ActionsActionType.BONUS, points=10)
    replacement = Actions(action_name="Mentor", ar_action_name="مرشد", action_type=ActionsActionType.BONUS, points=10)
    db_session.add_all([template, replacement])
    db_session.commit()

    assert_2xx(
        admin_points_client.post(
            "/bonuses",
            json={
                "title": "Hackathon",
                "given_at": "2026-07-05T10:00:00",
                "is_public": True,
                "grants": [
                    {
                        "recipient_type": "member",
                        "points": 10,
                        "source_action_id": template.id,
                        "member_ids": [seed_refs.ahmed.id],
                    }
                ],
            },
        )
    )

    blocked = admin_points_client.delete(f"/actions/{template.id}")
    assert blocked.status_code == 400

    assert_2xx(admin_points_client.delete(f"/actions/{template.id}", params={"replacement_id": replacement.id}))


def test_event_deletion_preview_shows_member_and_department_bonus_recipients(
    admin_points_client, db_session, seed_refs
):
    from app.services import event_deletion

    event = admin_points_client.post("/events", json=make_create_event_payload(seed_refs=seed_refs)).json()
    assert_2xx(
        admin_points_client.put(
            f"/bonuses/by-event/{event['id']}",
            json={
                "grants": [
                    {"recipient_type": "member", "points": 5, "member_ids": [seed_refs.ahmed.id]},
                    {"recipient_type": "department", "points": -3, "department_ids": [seed_refs.dept_business.id]},
                ]
            },
        )
    )

    page = event_deletion.resource_page(db_session, event["id"], "bonus_grant_recipients", page=1, page_size=20)
    labels = {item.label for item in page.items}
    assert seed_refs.ahmed.name in labels
    assert seed_refs.dept_business.name in labels


def test_bonus_validation_and_label_normalization(admin_points_client, seed_refs):
    # Whitespace-only title is rejected.
    bad_title = admin_points_client.post(
        "/bonuses",
        json={
            "title": "   ",
            "given_at": "2026-07-05T10:00:00",
            "is_public": True,
            "grants": [{"recipient_type": "member", "points": 5, "member_ids": [seed_refs.ahmed.id]}],
        },
    )
    assert bad_title.status_code == 422

    # Aware UTC timestamp normalizes cleanly to Riyadh naive time (+3h).
    created = admin_points_client.post(
        "/bonuses",
        json={
            "title": "  Spring Award  ",
            "given_at": "2026-07-05T07:00:00Z",
            "is_public": True,
            "grants": [
                {
                    "recipient_type": "member",
                    "points": 5,
                    "label_en": "Alpha",
                    "label_ar": "ألفا",
                    "member_ids": [seed_refs.ahmed.id],
                },
                {
                    "recipient_type": "member",
                    "points": 3,
                    "label_en": "Beta",
                    "label_ar": "بيتا",
                    "member_ids": [seed_refs.ahmed.id],
                },
            ],
        },
    )
    assert_2xx(created)
    bonus = created.json()
    assert bonus["title"] == "Spring Award"
    assert bonus["given_at"] == "2026-07-05T10:00:00"

    # PUT /bonuses/{id} rejects empty grants list and whitespace-only title.
    assert admin_points_client.put(f"/bonuses/{bonus['id']}", json={"grants": []}).status_code == 422
    assert admin_points_client.put(f"/bonuses/{bonus['id']}", json={"title": "   "}).status_code == 422


def test_custom_delete_prunes_empty_bonus_container(admin_points_client, db_session, seed_refs):
    created = admin_points_client.post(
        "/custom/members",
        json={
            "event_name": "One-off bonus",
            "start_datetime": "2026-07-07T10:00:00",
            "end_datetime": "2026-07-07T11:00:00",
            "location_type": "none",
            "point_deatils": [{"points": 4, "member_ids": [seed_refs.ahmed.id]}],
        },
    )
    assert_2xx(created)
    bonus_id = created.json()["bonus_id"]
    event_id = created.json()["event_id"]
    [row] = admin_points_client.get(f"/custom/members/{event_id}").json()["point_details"]

    assert_2xx(admin_points_client.delete(f"/custom/members/{row['log_id']}"))
    assert db_session.get(Bonuses, bonus_id) is None


def test_draft_event_bonus_does_not_queue_push(admin_points_client, db_session, seed_refs, monkeypatch):
    from app.config import config
    from app.DB.schema import PushKind, PushNotifications
    from tests.push_support import add_device

    monkeypatch.setattr(type(config), "push_enabled", property(lambda self: True))
    add_device(db_session, seed_refs.ahmed)
    db_session.commit()

    event = admin_points_client.post("/events", json=make_create_event_payload(seed_refs=seed_refs)).json()
    assert event["status"] == "draft"

    assert_2xx(
        admin_points_client.put(
            f"/bonuses/by-event/{event['id']}",
            json={"grants": [{"recipient_type": "member", "points": 5, "member_ids": [seed_refs.ahmed.id]}]},
        )
    )
    queued = db_session.scalars(
        select(PushNotifications).where(
            PushNotifications.kind == PushKind.POINTS, PushNotifications.event_id == event["id"]
        )
    ).all()
    assert queued == []


def test_events_me_joins_multiple_bonus_labels_with_comma_space(admin_points_client, clerk_client, seed_refs):
    me_id = clerk_client.get("/members/me").json()["id"]
    assert_2xx(
        admin_points_client.post(
            "/bonuses",
            json={
                "title": "Multi-grant Award",
                "given_at": "2026-07-05T10:00:00",
                "is_public": True,
                "grants": [
                    {
                        "recipient_type": "member",
                        "points": 5,
                        "label_en": "Beta",
                        "label_ar": "ب",
                        "member_ids": [me_id],
                    },
                    {
                        "recipient_type": "member",
                        "points": 3,
                        "label_en": "Alpha",
                        "label_ar": "أ",
                        "member_ids": [me_id],
                    },
                ],
            },
        )
    )
    bonuses = clerk_client.get("/events/me").json()["bonuses"]
    assert bonuses[0]["label_en"] == "Alpha, Beta"
    assert bonuses[0]["label_ar"] == "أ, ب"


def test_updating_event_syncs_attached_bonus_title_and_given_at(admin_points_client, db_session, seed_refs):
    payload = make_create_event_payload(seed_refs=seed_refs)
    event = admin_points_client.post("/events", json=payload).json()
    assert_2xx(
        admin_points_client.put(
            f"/bonuses/by-event/{event['id']}",
            json={"grants": [{"recipient_type": "member", "points": 5, "member_ids": [seed_refs.ahmed.id]}]},
        )
    )

    ev = payload["event"]
    actions = admin_points_client.get(f"/events/{event['id']}/details").json()["actions"]
    update_payload = {
        "event": {
            "name": "Renamed Workshop",
            "description": ev["description"],
            "location_type": ev["location_type"],
            "location": ev["location"],
            "start_datetime": "2026-07-10T14:00:00",
            "end_datetime": "2026-07-10T16:00:00",
            "status": "draft",
            "level": "beginner",
        },
        "actions": actions,
    }
    assert_2xx(admin_points_client.put(f"/events/{event['id']}", json=update_payload))

    db_session.expire_all()
    bonus = db_session.scalar(select(Bonuses).where(Bonuses.event_id == event["id"]))
    assert bonus is not None
    assert bonus.title == "Renamed Workshop"
    assert bonus.given_at == datetime(2026, 7, 10, 14, 0)
