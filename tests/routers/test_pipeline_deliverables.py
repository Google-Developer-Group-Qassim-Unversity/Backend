"""What the teams hand over: Logistics' confirmation, and the points tier the requesting team picks."""

import pytest
from sqlalchemy import select

from app.DB.schema import EventRequests, PipelineNotifications
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
        "event_type": "workshop",
        "description": "Hands-on machine learning",
    }
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
    assert moved.payload == {"from": ["2026-07-20", "2026-07-21"], "to": ["2026-07-22", "2026-07-22"]}
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


def finish_with(pipeline, world, **changes):
    assert save(pipeline, world, meet_link=MEET_LINK, **changes).status_code == 200
    return pipeline.client.post(url(world, "/tasks/logistics/complete"))


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
