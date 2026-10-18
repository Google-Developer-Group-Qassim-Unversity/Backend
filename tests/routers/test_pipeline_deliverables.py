"""What the teams hand over: Logistics' confirmation, Design's poster, Media's shares, and the points tier the requesting team picks."""

import pytest
from sqlalchemy import select

from app.DB.schema import EventRequests, PipelineHistory, PipelineHistoryAction, PipelineNotifications
from tests.pipeline_support import MEET_LINK, book_complete, finish, submit, upload_poster
from tests.r2_support import PNG


@pytest.fixture
def world(pipeline):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    ai = pipeline.department("AI")
    people = {
        "ai": pipeline.officer(ai),
        "design": pipeline.officer(design, name="Designer"),
        "logistics": pipeline.officer(logistics, name="Logistics"),
        "media": pipeline.officer(media, name="Media"),
    }
    pipeline.sign_in(people["ai"])
    request_id = book_complete(pipeline, ai)
    assert submit(pipeline, request_id).status_code == 200
    pipeline.sign_in(people["logistics"])
    return {"request_id": request_id, "people": people, "ai": ai}


def url(world, tail=""):
    return f"/pipeline/requests/{world['request_id']}{tail}"


def logistics_task(body):
    return next(t for t in body["tasks"] if t["team"] == "logistics")


def save(pipeline, world, **changes):
    current = logistics_task(pipeline.client.get(url(world)).json())["deliverable"]
    return pipeline.client.put(url(world, "/deliverables/logistics"), json={"deliverable": {**current, **changes}})


# --------------------------------------------------------------------------- Logistics' confirmation


def test_the_confirmation_opens_prefilled_from_the_request(pipeline, world):
    task = logistics_task(pipeline.client.get(url(world)).json())
    assert task["deliverable"] == {
        "start_date": "2026-07-20",
        "end_date": "2026-07-21",
        "day_modes": {"2026-07-20": "on_site", "2026-07-21": "online"},
        "daily_start_time": "10:00:00",
        "daily_end_time": "12:00:00",
        "venue": "التيك فالي (60)",
        "room": None,
        "meet_link": None,
        "meet_event_id": None,
        "event_type": "workshop",
        "description": "Hands-on machine learning",
        "change_reason": None,
    }
    assert task["confirmation_changes"] == []
    # One day is online, and only Logistics knows the Meet link.
    assert task["deliverable_missing"] == ["confirm.meet_link"]


def test_logistics_cannot_confirm_until_it_is_complete(pipeline, world):
    response = pipeline.client.post(url(world, "/tasks/logistics/complete"))
    assert response.status_code == 422
    assert response.json()["code"] == "incomplete"
    assert [e["loc"] for e in response.json()["detail"]] == [["confirm", "meet_link"]]

    assert save(pipeline, world, meet_link="not a link").status_code == 200  # a draft saves as it is
    assert pipeline.client.post(url(world, "/tasks/logistics/complete")).status_code == 422

    assert save(pipeline, world, meet_link=MEET_LINK).status_code == 200
    body = pipeline.client.post(url(world, "/tasks/logistics/complete")).json()
    assert logistics_task(body)["status"] == "done"
    assert logistics_task(body)["deliverable"]["meet_link"] == MEET_LINK


def test_an_on_site_day_needs_a_venue(pipeline, world):
    body = save(pipeline, world, venue="", meet_link=MEET_LINK).json()
    assert logistics_task(body)["deliverable_missing"] == ["confirm.venue"]


def test_a_value_of_the_wrong_type_is_refused(pipeline, world):
    response = save(pipeline, world, daily_start_time="soon")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "deliverable", "daily_start_time"]


def test_only_logistics_fills_in_the_confirmation(pipeline, world):
    pipeline.sign_in(world["people"]["design"])
    assert save(pipeline, world, meet_link=MEET_LINK).status_code == 403
    pipeline.sign_in(world["people"]["ai"])
    assert save(pipeline, world, meet_link=MEET_LINK).status_code == 403


def test_confirming_other_dates_moves_the_request_and_tells_the_department(pipeline, world):
    response = finish_with(
        pipeline, world, start_date="2026-07-22", end_date="2026-07-22", day_modes={"2026-07-22": "online"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["start_date"], body["end_date"]) == ("2026-07-22", "2026-07-22")

    request = pipeline.session.get(EventRequests, world["request_id"])
    pipeline.session.refresh(request)
    assert request.day_modes == {"2026-07-22": "online"}
    days = pipeline.client.get("/pipeline/calendar", params={"from": "2026-07-20", "to": "2026-07-22"}).json()["days"]
    assert [d["status"] for d in days] == ["open", "open", "booked"]

    moved = pipeline.session.scalar(
        select(PipelineNotifications).where(
            PipelineNotifications.request_id == world["request_id"], PipelineNotifications.kind == "dates_changed"
        )
    )
    assert moved.department_id == world["ai"].id
    assert moved.payload == {
        "from": ["2026-07-20", "2026-07-21"],
        "to": ["2026-07-22", "2026-07-22"],
        "changes": [
            {"field": "start_date", "from": "2026-07-20", "to": "2026-07-22"},
            {"field": "end_date", "from": "2026-07-21", "to": "2026-07-22"},
        ],
        "reason": REASON,
    }
    subjects = [b.params["subject"] for b in pipeline.outbound.to("/blasts")]
    assert any("dates changed" in s.lower() for s in subjects)


def test_logistics_cannot_move_a_request_onto_a_taken_day(pipeline, world):
    other = pipeline.department("Cyber")
    pipeline.sign_in(pipeline.officer(other))
    taken = pipeline.client.post(
        "/pipeline/requests", json={"department_id": other.id, "start_date": "2026-07-23", "end_date": "2026-07-23"}
    )
    assert taken.status_code == 201
    pipeline.sign_in(world["people"]["logistics"])

    response = finish_with(
        pipeline, world, start_date="2026-07-23", end_date="2026-07-23", day_modes={"2026-07-23": "online"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "day_taken"
    task = logistics_task(pipeline.client.get(url(world)).json())
    assert task["status"] == "open"


REASON = "The hall was taken that day; the university offered Wednesday instead."


def finish_with(pipeline, world, **changes):
    """Confirm with ``changes`` to the prefilled form, giving the reason any change needs."""
    assert save(pipeline, world, meet_link=MEET_LINK, change_reason=REASON, **changes).status_code == 200
    return pipeline.client.post(url(world, "/tasks/logistics/complete"))


# --------------------------------------------------------------------------- changes need a reason


def test_a_change_from_the_request_needs_a_reason(pipeline, world):
    body = save(pipeline, world, meet_link=MEET_LINK, daily_start_time="11:00").json()
    task = logistics_task(body)
    assert task["confirmation_changes"] == ["daily_start_time"]
    assert task["deliverable_missing"] == ["confirm.change_reason"]

    response = pipeline.client.post(url(world, "/tasks/logistics/complete"))
    assert response.status_code == 422
    assert [e["loc"] for e in response.json()["detail"]] == [["confirm", "change_reason"]]

    assert save(pipeline, world, change_reason="  ").status_code == 200  # blank is still missing
    assert pipeline.client.post(url(world, "/tasks/logistics/complete")).status_code == 422
    assert save(pipeline, world, change_reason=REASON).status_code == 200
    body = pipeline.client.post(url(world, "/tasks/logistics/complete")).json()
    assert logistics_task(body)["status"] == "done"
    assert logistics_task(body)["deliverable"]["change_reason"] == REASON


def test_the_department_hears_what_changed_and_why(pipeline, world):
    response = finish_with(pipeline, world, daily_start_time="11:00", venue="بيت الثقافة")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["start_date"], body["end_date"]) == ("2026-07-20", "2026-07-21")  # the dates did not move

    changed = pipeline.session.scalar(
        select(PipelineNotifications).where(
            PipelineNotifications.request_id == world["request_id"],
            PipelineNotifications.kind == "confirmation_changed",
        )
    )
    assert changed.department_id == world["ai"].id
    assert changed.payload == {
        "changes": [
            {"field": "daily_start_time", "from": "10:00:00", "to": "11:00:00"},
            {"field": "venue", "from": "التيك فالي (60)", "to": "بيت الثقافة"},
        ],
        "reason": REASON,
    }
    assert not pipeline.session.scalar(
        select(PipelineNotifications).where(
            PipelineNotifications.request_id == world["request_id"], PipelineNotifications.kind == "dates_changed"
        )
    )
    blast = next(b for b in pipeline.outbound.to("/blasts") if "details changed" in b.params["subject"].lower())
    html = blast.text
    assert REASON in html
    assert "Start time" in html and "10:00" in html and "11:00" in html
    assert "بيت الثقافة" in html

    done = next(h for h in body["history"] if h["action"] == "task_done" and h["details"]["team"] == "logistics")
    assert done["details"]["reason"] == REASON
    assert [c["field"] for c in done["details"]["changes"]] == ["daily_start_time", "venue"]


def test_confirming_as_requested_needs_no_reason_and_sends_nothing(pipeline, world):
    assert save(pipeline, world, meet_link=MEET_LINK, room="B12").status_code == 200  # filling a blank
    task = logistics_task(pipeline.client.get(url(world)).json())
    assert task["confirmation_changes"] == []
    assert task["deliverable_missing"] == []
    body = pipeline.client.post(url(world, "/tasks/logistics/complete")).json()
    assert logistics_task(body)["status"] == "done"
    kinds = set(
        pipeline.session.scalars(
            select(PipelineNotifications.kind).where(PipelineNotifications.request_id == world["request_id"])
        ).all()
    )
    assert not kinds & {"confirmation_changed", "dates_changed"}


def test_a_change_undone_drops_its_reason(pipeline, world):
    assert save(pipeline, world, meet_link=MEET_LINK, daily_start_time="11:00", change_reason=REASON).status_code == 200
    body = save(pipeline, world, daily_start_time="10:00").json()
    assert logistics_task(body)["confirmation_changes"] == []
    body = pipeline.client.post(url(world, "/tasks/logistics/complete")).json()
    assert logistics_task(body)["deliverable"]["change_reason"] is None


# --------------------------------------------------------------------------- points tier


def test_submit_needs_a_points_tier(pipeline):
    ai = pipeline.department("AI")
    pipeline.sign_in(pipeline.officer(ai))
    request_id = book_complete(pipeline, ai)
    request_url = f"/pipeline/requests/{request_id}"
    cleared = pipeline.client.put(
        f"{request_url}/details", json={"department_action_id": None, "member_action_id": None}
    )
    assert "details.points_tier" in cleared.json()["missing"]


def test_the_points_tier_must_be_one_of_the_pairs(pipeline, seed_refs):
    ai = pipeline.department("AI")
    pipeline.sign_in(pipeline.officer(ai))
    request_id = book_complete(pipeline, ai)
    swapped = {"department_action_id": seed_refs.member_action.id, "member_action_id": seed_refs.dept_action.id}
    response = pipeline.client.put(f"/pipeline/requests/{request_id}/details", json=swapped)
    assert response.status_code == 422
    assert response.json()["code"] == "not_a_points_tier"


def test_finished_teams_hand_over_nothing_more(pipeline, world):
    assert finish(pipeline, world["request_id"], "logistics").status_code == 200
    assert save(pipeline, world, room="B12").status_code == 409


# --------------------------------------------------------------------------- Design's poster


def design_task(body):
    return next(t for t in body["tasks"] if t["team"] == "design")


def test_design_cannot_finish_without_a_poster(pipeline, world):
    pipeline.sign_in(world["people"]["design"])
    body = pipeline.client.get(url(world)).json()
    assert design_task(body)["deliverable_missing"] == ["poster.poster_url"]
    assert body["actions"]["can_upload_poster"] is True
    response = pipeline.client.post(url(world, "/tasks/design/complete"))
    assert response.status_code == 422
    assert [e["loc"] for e in response.json()["detail"]] == [["poster", "poster_url"]]


def test_the_poster_lands_in_r2_and_on_the_request(pipeline, world, fake_r2):
    pipeline.sign_in(world["people"]["design"])
    response = upload_poster(pipeline, world["request_id"])
    assert response.status_code == 200, response.text
    poster_url = design_task(response.json())["deliverable"]["poster_url"]
    assert poster_url.startswith("https://cdn.example.com/event-images/") and poster_url.endswith(".png")
    key = poster_url.removeprefix("https://cdn.example.com/")
    assert fake_r2.get_object(Bucket="test-bucket", Key=key)["Body"].read() == PNG
    assert pipeline.client.post(url(world, "/tasks/design/complete")).status_code == 200


def test_design_can_replace_the_poster_until_publish(pipeline, world):
    pipeline.sign_in(world["people"]["design"])
    first = design_task(upload_poster(pipeline, world["request_id"]).json())["deliverable"]["poster_url"]
    assert pipeline.client.post(url(world, "/tasks/design/complete")).status_code == 200
    second = design_task(upload_poster(pipeline, world["request_id"]).json())["deliverable"]["poster_url"]
    assert second != first


def test_only_design_uploads_the_poster(pipeline, world):
    assert upload_poster(pipeline, world["request_id"]).status_code == 403  # signed in as Logistics
    pipeline.sign_in(world["people"]["ai"])
    assert upload_poster(pipeline, world["request_id"]).status_code == 403


def test_the_poster_must_be_an_image(pipeline, world):
    pipeline.sign_in(world["people"]["design"])
    response = upload_poster(pipeline, world["request_id"], b"%PDF-1.4", "application/pdf")
    assert response.status_code == 422
    assert response.json()["code"] == "not_an_image"


# --------------------------------------------------------------------------- Media's shares


def media_task(body):
    return next(t for t in body["tasks"] if t["team"] == "media")


@pytest.fixture
def at_media(pipeline, world):
    """Design has handed over its poster, so Media's part is open."""
    pipeline.sign_in(world["people"]["design"])
    assert finish(pipeline, world["request_id"], "design").status_code == 200
    pipeline.sign_in(world["people"]["media"])
    return world


def share(pipeline, world, **deliverable):
    return pipeline.client.put(url(world, "/deliverables/media"), json={"deliverable": deliverable})


def test_media_opens_with_nothing_shared_and_can_finish_so(pipeline, at_media):
    task = media_task(pipeline.client.get(url(at_media)).json())
    assert task["deliverable"] == {"platforms": [], "other_platform": None, "links": {}}
    assert task["deliverable_missing"] == []
    assert pipeline.client.post(url(at_media, "/tasks/media/complete")).status_code == 200


def test_media_keeps_where_it_shared_with_the_links(pipeline, db_session, at_media):
    body = share(
        pipeline,
        at_media,
        platforms=["tiktok", "x", "instagram", "x"],
        links={"x": " https://x.com/gdgq/status/1 ", "tiktok": ""},
    ).json()
    # The form's order, each once; a blank link is no link.
    assert media_task(body)["deliverable"] == {
        "platforms": ["x", "instagram", "tiktok"],
        "other_platform": None,
        "links": {"x": "https://x.com/gdgq/status/1"},
    }
    assert pipeline.client.post(url(at_media, "/tasks/media/complete")).status_code == 200
    done = db_session.scalars(
        select(PipelineHistory.details).where(
            PipelineHistory.request_id == at_media["request_id"],
            PipelineHistory.action == PipelineHistoryAction.TASK_DONE,
        )
    ).all()
    assert {"team": "media", "platforms": ["x", "instagram", "tiktok"]} in done


def test_an_unticked_platform_keeps_no_link_or_name(pipeline, at_media):
    body = share(
        pipeline, at_media, platforms=["x"], other_platform="LinkedIn", links={"instagram": "https://instagram.com/p/1"}
    ).json()
    assert media_task(body)["deliverable"] == {"platforms": ["x"], "other_platform": None, "links": {}}


def test_somewhere_else_needs_its_name_and_a_link_must_be_a_link(pipeline, at_media):
    body = share(pipeline, at_media, platforms=["other", "instagram"], links={"instagram": "my story"}).json()
    assert media_task(body)["deliverable_missing"] == ["share.other_platform", "share.links.instagram"]
    response = pipeline.client.post(url(at_media, "/tasks/media/complete"))
    assert response.status_code == 422
    assert response.json()["code"] == "incomplete"

    share(
        pipeline,
        at_media,
        platforms=["other", "instagram"],
        other_platform="LinkedIn",
        links={"instagram": "https://instagram.com/p/1"},
    )
    assert pipeline.client.post(url(at_media, "/tasks/media/complete")).status_code == 200


def test_an_unknown_platform_is_refused(pipeline, at_media):
    response = share(pipeline, at_media, platforms=["myspace"])
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:3] == ["body", "deliverable", "platforms"]


def test_only_media_says_where_it_shared(pipeline, at_media):
    pipeline.sign_in(at_media["people"]["design"])
    assert share(pipeline, at_media, platforms=["x"]).status_code == 403
    pipeline.sign_in(at_media["people"]["ai"])
    assert share(pipeline, at_media, platforms=["x"]).status_code == 403


def test_design_still_hands_over_no_form(pipeline, at_media):
    pipeline.sign_in(at_media["people"]["design"])
    response = pipeline.client.put(url(at_media, "/deliverables/design"), json={"deliverable": {}})
    assert response.status_code == 422
    assert response.json()["code"] == "no_form"
