"""What is the caller's own and what they only oversee: own teams, team desks, quieter super admins."""

import pytest

from tests.pipeline_support import COMPLETE_DESIGN, COMPLETE_DETAILS, COMPLETE_LOGISTICS, book_complete, finish, submit


@pytest.fixture
def world(pipeline):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    ai, web = pipeline.department("AI"), pipeline.department("Web")
    people = {
        "ai": pipeline.officer(ai),
        "web": pipeline.officer(web),
        "design": pipeline.officer(design, name="Designer"),
        "logistics": pipeline.officer(logistics, name="Logistics"),
        "media": pipeline.officer(media, name="Media"),
        # A super admin who also leads AI: AI is theirs, everything else they oversee.
        "admin": pipeline.officer(ai, name="Admin"),
        # A super admin on no roster at all.
        "outsider": pipeline.person("Outsider"),
    }
    pipeline.sign_in(people["admin"], super_admin=True)
    pipeline.sign_in(people["outsider"], super_admin=True)
    return {"people": people, "ai": ai, "web": web, "design": design, "logistics": logistics}


def book_on(pipeline, department, day: str, complete: bool = True) -> str:
    """Book one day for ``department``; ``book_complete`` fills in a fixed pair of days."""
    response = pipeline.client.post(
        "/pipeline/requests", json={"department_id": department.id, "start_date": day, "end_date": day}
    )
    assert response.status_code == 201, response.text
    request_id = response.json()["id"]
    if complete:
        url = f"/pipeline/requests/{request_id}"
        details = {**COMPLETE_DETAILS, **pipeline.tier, "day_modes": {day: "on_site"}}
        assert pipeline.client.put(f"{url}/details", json=details).status_code == 200
        assert pipeline.client.put(f"{url}/briefs/design", json={"brief": COMPLETE_DESIGN}).status_code == 200
        assert pipeline.client.put(f"{url}/briefs/logistics", json={"brief": COMPLETE_LOGISTICS}).status_code == 200
    return request_id


def as_(pipeline, world, who):
    pipeline.sign_in(world["people"][who])


def me(pipeline):
    return pipeline.client.get("/pipeline/me").json()


def test_own_teams_and_departments_are_what_the_roster_gives(pipeline, world):
    as_(pipeline, world, "design")
    body = me(pipeline)
    assert body["own_teams"] == ["design"]
    assert body["own_department_ids"] == [world["design"].id]

    as_(pipeline, world, "logistics")
    assert me(pipeline)["own_teams"] == ["logistics"]


def test_a_super_admin_owns_only_their_roster(pipeline, world):
    as_(pipeline, world, "admin")
    body = me(pipeline)
    assert body["is_super_admin"] is True
    assert body["own_teams"] == []
    assert body["own_department_ids"] == [world["ai"].id]
    # They can still act for every department.
    assert {d["id"] for d in body["departments"]} >= {world["ai"].id, world["web"].id, world["design"].id}

    as_(pipeline, world, "outsider")
    assert me(pipeline)["own_department_ids"] == []


def test_rows_say_where_each_teams_part_stands(pipeline, world):
    as_(pipeline, world, "web")
    request_id = book_complete(pipeline, world["web"])
    row = pipeline.client.get("/pipeline/requests").json()["items"][0]
    assert [(t["team"], t["status"]) for t in row["task_states"]] == [("design", "brief"), ("logistics", "brief")]

    submit(pipeline, request_id)
    as_(pipeline, world, "logistics")
    assert finish(pipeline, request_id, "logistics").status_code == 200
    as_(pipeline, world, "web")
    row = pipeline.client.get("/pipeline/requests").json()["items"][0]
    states = {t["team"]: t for t in row["task_states"]}
    assert states["design"]["status"] == "open"
    assert states["logistics"]["status"] == "done" and states["logistics"]["done_at"]


def test_a_team_desk_lists_what_reached_the_team_from_any_department(pipeline, world):
    as_(pipeline, world, "web")
    web_request = book_complete(pipeline, world["web"])
    submit(pipeline, web_request)
    as_(pipeline, world, "ai")
    ai_request = book_on(pipeline, world["ai"], "2026-07-28")
    assert submit(pipeline, ai_request).status_code == 200
    ai_draft = book_on(pipeline, world["ai"], "2026-07-27")

    as_(pipeline, world, "design")
    desk = pipeline.client.get("/pipeline/requests", params={"team": "design"}).json()
    ids = {r["id"] for r in desk["items"]}
    assert ids == {web_request, ai_request}  # never the draft: it has not reached Design
    assert ai_draft not in ids

    assert finish(pipeline, web_request, "design").status_code == 200
    done = pipeline.client.get("/pipeline/requests", params={"team": "design", "team_status": "done"}).json()
    assert [r["id"] for r in done["items"]] == [web_request]
    waiting = pipeline.client.get("/pipeline/requests", params={"team": "design", "team_status": "open"}).json()
    assert [r["id"] for r in waiting["items"]] == [ai_request]


def test_only_a_team_opens_its_desk(pipeline, world):
    as_(pipeline, world, "ai")
    assert pipeline.client.get("/pipeline/requests", params={"team": "design"}).status_code == 403
    as_(pipeline, world, "logistics")
    assert pipeline.client.get("/pipeline/requests", params={"team": "design"}).status_code == 403
    as_(pipeline, world, "outsider")
    assert pipeline.client.get("/pipeline/requests", params={"team": "design"}).status_code == 200


def test_past_is_what_was_published_or_cancelled(pipeline, world):
    as_(pipeline, world, "web")
    gone = book_on(pipeline, world["web"], "2026-07-27", complete=False)
    assert pipeline.client.delete(f"/pipeline/requests/{gone}").status_code == 200
    kept = book_on(pipeline, world["web"], "2026-07-28", complete=False)

    active = pipeline.client.get("/pipeline/requests", params={"state": "active"}).json()
    past = pipeline.client.get("/pipeline/requests", params={"state": "past"}).json()
    assert [r["id"] for r in active["items"]] == [kept]
    assert [r["id"] for r in past["items"]] == [gone]
    # Without a state, cancelled requests stay out of the way as before.
    assert [r["id"] for r in pipeline.client.get("/pipeline/requests").json()["items"]] == [kept]


def test_several_departments_at_once(pipeline, world):
    as_(pipeline, world, "web")
    web_request = book_complete(pipeline, world["web"])
    as_(pipeline, world, "ai")
    ai_request = book_on(pipeline, world["ai"], "2026-07-27", complete=False)

    as_(pipeline, world, "outsider")
    both = pipeline.client.get(
        "/pipeline/requests", params=[("department_id", world["web"].id), ("department_id", world["ai"].id)]
    ).json()
    assert {r["id"] for r in both["items"]} == {web_request, ai_request}

    as_(pipeline, world, "ai")
    refused = pipeline.client.get(
        "/pipeline/requests", params=[("department_id", world["web"].id), ("department_id", world["ai"].id)]
    )
    assert refused.status_code == 403


def test_a_super_admin_is_notified_only_about_their_own(pipeline, world):
    as_(pipeline, world, "web")
    submit(pipeline, book_complete(pipeline, world["web"]))  # tells Design and Logistics

    # The admin leads AI and is on no team: Web's request reaching Design is not their news.
    as_(pipeline, world, "admin")
    mine = pipeline.client.get("/pipeline/notifications").json()
    assert mine["total"] == 0 and mine["unread"] == 0
    club = pipeline.client.get("/pipeline/notifications", params={"scope": "club"}).json()
    assert {n["kind"] for n in club["items"]} == {"request_received"}
    assert club["total"] == 2

    # Read-all marks only what was asked for.
    assert pipeline.client.post("/pipeline/notifications/read-all").json()["count"] == 0
    assert pipeline.client.post("/pipeline/notifications/read-all", params={"scope": "club"}).json()["count"] == 2

    # Their own department's news still reaches them.
    as_(pipeline, world, "ai")
    request_id = book_on(pipeline, world["ai"], "2026-07-27", complete=False)
    as_(pipeline, world, "logistics")
    pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-27"], "reason": "Exams"})
    as_(pipeline, world, "admin")
    items = pipeline.client.get("/pipeline/notifications").json()["items"]
    assert [(n["kind"], n["request"]["id"]) for n in items] == [("dates_banned", request_id)]


def test_everyone_else_gets_the_same_notifications_either_way(pipeline, world):
    as_(pipeline, world, "web")
    submit(pipeline, book_complete(pipeline, world["web"]))
    as_(pipeline, world, "design")
    mine = pipeline.client.get("/pipeline/notifications").json()
    club = pipeline.client.get("/pipeline/notifications", params={"scope": "club"}).json()
    assert mine["total"] == club["total"] == 1


def test_staff_see_their_own_access_explained(pipeline, world):
    as_(pipeline, world, "design")
    body = pipeline.client.get("/permissions/me").json()
    assert body["member"]["member_id"] == world["people"]["design"].id
    assert body["is_super_admin"] is False
    [department] = body["departments"]
    assert department["department_id"] == world["design"].id
    assert "leader" in department["roles"]
    assert any(p["permission"] == "pipeline.design" and "team" in p["sources"] for p in department["permissions"])

    as_(pipeline, world, "admin")
    assert pipeline.client.get("/permissions/me").json()["is_super_admin"] is True
