"""One member, start to finish, across every router that touches them.

`tests/routers/` mirrors `app/routers/` one file per router, which pins each
endpoint but leaves the seams between them unowned: nothing there fails if
attendance stops feeding points, if creating an event stops poking the member
app's cache, or if signing up stops finding the points an admin already
awarded. Those are the failures that reach members, and each one lives
between two files that both still pass.

So this file arranges as little as it can by hand and asserts through public
endpoints only - the same ones the member app calls - in the order a real
member hits them.

**On identity.** The auth fixtures in conftest stack their overrides onto a
single TestClient, so a journey can't hold an admin and a member identity at
once: `CurrentMember` is always the member behind FAKE_CLERK_CREDENTIALS.
Who is *allowed* to call what is `tests/test_route_auth.py`'s job. This file
is about what happens to the data once they do.
"""

import pytest
from fastapi.testclient import TestClient

from app.DB.schema import Actions, ActionsActionType
from tests.factories import make_create_event_payload, make_event
from tests.outbound import CERTIFICATE_FILE_BYTES, CERTIFICATE_FILE_URL
from tests.utils import assert_2xx

# The metadata on FAKE_CLERK_CREDENTIALS - what Clerk hands us on first sign-in.
CLERK_UNI_ID = "123456789"
CLERK_EMAIL = "test@example.com"
CLERK_NAME = "Test Member"

# `log_queries.get_attendable_logs` decides whether an event can be attended by
# testing the log's action against a hardcoded list of production row IDs:
#
#     ATTENDABLE_ACTION_IDS = [76, 77, 78, 79, 87, 89]
#
# so an event built on the seeded actions (IDs 1 and 2) is unattendable and the
# endpoint 500s with "Event has no attendable logs". tests/routers/test_attendance.py
# works around it by swapping the function out for the whole module, which means
# the real lookup never runs in the suite at all.
#
# A journey test can't do that - the point is to exercise the path members
# actually take - so it seeds an action at one of the IDs production considers
# attendable instead. The production code runs unpatched; the fixture is the
# thing that bends. When those IDs become configurable this constant goes away.
ATTENDABLE_ACTION_ID = 76
ATTENDANCE_POINTS = 5


@pytest.fixture
def api(admin_client, super_admin_client) -> TestClient:
    """One client with the admin, super-admin and member guards all satisfied."""
    return admin_client


@pytest.fixture
def attendable_action(db_session) -> Actions:
    """A member action at an ID `get_attendable_logs` will accept."""
    action = Actions(
        id=ATTENDABLE_ACTION_ID,
        action_name="attendable on-site course attendance",
        points=ATTENDANCE_POINTS,
        action_type=ActionsActionType.MEMBER,
        ar_action_name="حضور دورة حضورية",
    )
    db_session.add(action)
    db_session.commit()
    return action


# ---------- steps, in the order a member meets them ----------


def sign_up(api: TestClient) -> dict:
    """First authenticated request: `POST /members/` mints the member row."""
    response = api.post("/members/")
    assert_2xx(response)
    return response.json()


def create_event(api: TestClient, seed_refs, **overrides) -> dict:
    """A published, attendable event.

    Two details the factory defaults get wrong for a journey:

    - `status` defaults to "draft", and the leaderboard SQL excludes draft
      events (`e.status <> 'draft'`), so points earned at one are invisible.
    - `member_action_id` has to be an ID `get_attendable_logs` accepts.
    """
    overrides.setdefault("event", make_event(status="open"))
    overrides.setdefault("member_action_id", ATTENDABLE_ACTION_ID)
    response = api.post("/events/", json=make_create_event_payload(seed_refs, **overrides))
    assert_2xx(response)
    return response.json()


def event_form(api: TestClient, event_id: int) -> dict:
    response = api.get(f"/events/{event_id}/form")
    assert_2xx(response)
    return response.json()


def register(api: TestClient, form_id: int) -> dict:
    response = api.post(f"/submissions/{form_id}", params={"submission_type": "none"})
    assert_2xx(response)
    return response.json()


def accept(api: TestClient, submission_id: int) -> None:
    response = api.put("/submissions/accept", json=[{"submission_id": submission_id, "is_accepted": True}])
    assert_2xx(response)


def send_acceptance_blast(api: TestClient, event_id: int, subject: str, html: str) -> dict:
    response = api.post(
        f"/emails/acceptance/blasts/{event_id}",
        params={"subject": subject},
        content=html.encode(),
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    assert_2xx(response)
    return response.json()


def mark_attended(api: TestClient, event_id: int, member_id: int, day: int = 1) -> dict:
    response = api.post(f"/attendance/{event_id}/manual", json={"member_ids": [member_id], "day": day})
    assert_2xx(response)
    return response.json()


def total_points(api: TestClient, member_id: int) -> int:
    """What the leaderboard shows for one member, read the way the member app reads it."""
    response = api.get("/points/members/total")
    assert_2xx(response)
    rows = {row["member_id"]: row["total_points"] for row in response.json()}
    return rows.get(member_id, 0)


def point_history(api: TestClient, member_id: int) -> list[dict]:
    response = api.get(f"/points/members/{member_id}")
    assert_2xx(response)
    return response.json()["events"]


def give_bonus(api: TestClient, event_id: int, member_id: int, points: int) -> None:
    response = api.post(
        "/custom/members",
        json={
            "event_id": event_id,
            "event_name": "ignored when event_id is set",
            "location_type": "online",
            "start_datetime": "2026-06-29T00:00:00",
            "end_datetime": "2026-06-29T00:00:00",
            "point_deatils": [{"member_ids": [member_id], "points": points}],
        },
    )
    assert_2xx(response)


# ---------- the journey ----------


def test_member_signs_up_attends_and_ends_up_on_the_leaderboard(api, seed_refs, attendable_action, outbound):
    """Sign up -> register -> accepted -> attended -> points -> certificate -> download."""

    # 1. First sign-in mints the member from Clerk's metadata.
    signup = sign_up(api)
    member = signup["member"]
    assert signup["already_exists"] is False
    assert member["uni_id"] == CLERK_UNI_ID
    assert member["email"] == CLERK_EMAIL

    # 2. An admin creates the event. This also resets the member app's Next.js
    #    data cache - a seam with no other coverage, and the reason a new event
    #    can appear in the DB but not on the leaderboard.
    event = create_event(api, seed_refs)
    revalidate = outbound.one("/api/revalidate")
    assert revalidate.headers["authorization"] == "Bearer test_revalidate_secret"

    # 3. The member registers through the form created alongside the event.
    form = event_form(api, event["id"])
    assert form["event_id"] == event["id"]
    submission = register(api, form["id"])
    assert submission["is_accepted"] == 0
    assert submission["is_invited"] == 0

    # 4. An admin accepts them.
    accept(api, submission["id"])

    # 5. The acceptance email goes out. Assert the *request*, not that a mail
    #    arrived: this is the wire contract with send-certificates' POST /blasts,
    #    and the shape of it (repeated `emails`, HTML in the body, no empty-string
    #    params) is what broke before.
    html = "<p>You're in.</p>"
    result = send_acceptance_blast(api, event["id"], subject="You're in", html=html)
    assert result == {"sent_count": 1, "emails": [CLERK_EMAIL]}

    blast = outbound.one("/blasts")
    assert blast.method == "POST"
    assert blast.params.get_list("emails") == [CLERK_EMAIL]
    assert blast.params["subject"] == "You're in"
    assert blast.params["provider"] == "google"
    assert blast.params["from_address"] == "gdg.qu1@gmail.com"
    assert blast.text == html
    assert blast.headers["content-type"].startswith("text/html")

    # Sending again must not re-invite anyone: the first send marked the
    # submission invited, and `get_accepted_not_invited_by_event` is the only
    # thing standing between a member and a duplicate acceptance email.
    #
    # It must also not call out at all. An empty recipient list reaches
    # send-certificates as a request with no `emails` key (httpx omits it), and
    # the required param there turns that into a 422 the admin sees as a 502 -
    # so "nobody left to invite" has to short-circuit before the gateway call,
    # not through it. tests/outbound.py answers an empty `emails` with the same
    # 422 the real service does, so removing that guard fails this test.
    assert send_acceptance_blast(api, event["id"], subject="You're in", html=html) == {"sent_count": 0, "emails": []}
    assert len(outbound.to("/blasts")) == 1

    # 6. An admin marks them attended.
    assert mark_attended(api, event["id"], member["id"]) == {"success": 1, "failed": 0}

    # 7. The attendance is worth the member action's points, and shows up on the
    #    public leaderboard - not just in the attendance table.
    assert total_points(api, member["id"]) == ATTENDANCE_POINTS

    history = point_history(api, member["id"])
    assert [row["event_id"] for row in history] == [event["id"]]
    assert history[0]["points"] == ATTENDANCE_POINTS

    # 8. An admin sends certificates for the event. The send runs in a background
    #    task, which TestClient drains before returning, so the outbound call is
    #    already recorded here.
    response = api.post(f"/emails/{event['id']}")
    assert_2xx(response)
    assert response.json()["recipient_count"] == 1

    certificate = outbound.one("/emails/certificate")
    assert certificate.json == {
        "event": {"name": event["name"], "date": certificate.json["event"]["date"], "official": False},
        "member": {"name": CLERK_NAME, "email": CLERK_EMAIL, "gender": "Male"},
        "language": "ar",
        "provider": "google",
        "from_address": "gdg.qu1@gmail.com",
    }

    # 9. The member downloads it themselves. The backend asks the certificate API
    #    to generate one, then streams back whatever file it points at.
    response = api.post(f"/emails/download-certificate/{event['id']}", params={"lang": "ar", "format": "pdf"})
    assert_2xx(response)
    assert response.content == CERTIFICATE_FILE_BYTES
    assert response.headers["content-disposition"].startswith("attachment;")

    generation = outbound.one("/generations/certificate")
    assert generation.json["format"] == "pdf"
    assert generation.json["language"] == "ar"
    assert generation.json["member"]["email"] == CLERK_EMAIL
    assert CERTIFICATE_FILE_URL in [str(call.url) for call in outbound.calls]


def test_bonus_points_from_an_admin_land_on_the_public_total(api, seed_refs, attendable_action, outbound):
    """Attendance points and a manual bonus have to add up, not overwrite."""
    member = sign_up(api)["member"]
    event = create_event(api, seed_refs)

    mark_attended(api, event["id"], member["id"])
    earned = total_points(api, member["id"])
    assert earned == ATTENDANCE_POINTS

    give_bonus(api, event["id"], member["id"], points=7)

    assert total_points(api, member["id"]) == earned + 7
    assert sum(row["points"] for row in point_history(api, member["id"])) == earned + 7


def test_a_discount_is_subtracted_rather_than_added(api, seed_refs, attendable_action, outbound):
    """`give_member_custom_points` infers bonus vs discount from the sign, and the
    points query has separate SQL branches for the two - so a negative value that
    silently added would pass every per-router test."""
    member = sign_up(api)["member"]
    event = create_event(api, seed_refs)
    mark_attended(api, event["id"], member["id"])
    earned = total_points(api, member["id"])

    give_bonus(api, event["id"], member["id"], points=-3)

    assert total_points(api, member["id"]) == earned - 3


# ---------- points that predate the account ----------
#
# An admin can award points to someone who has never signed in, by creating a
# shadow member row. When that person later signs up, `create_member_if_not_exists`
# has to fold their Clerk identity into the existing row rather than mint a
# second one - otherwise the points are stranded on a row nobody can log into,
# and the member sees zero. It matches on uni_id first, then on an unclaimed
# email, so both routes need pinning.


def create_shadow_member(api: TestClient, **overrides) -> dict:
    """A member an admin created by hand: real row, no Clerk identity yet."""
    payload = {
        "name": "Walk-in Attendee",
        "email": CLERK_EMAIL,
        "phone_number": "0501234567",
        "uni_id": CLERK_UNI_ID,
        "gender": "Male",
    }
    payload.update(overrides)
    response = api.post("/members/manual", json=payload)
    assert_2xx(response)
    return response.json()["member"]


def test_points_awarded_before_signup_survive_the_first_sign_in_by_uni_id(api, seed_refs, attendable_action, outbound):
    shadow = create_shadow_member(api)
    event = create_event(api, seed_refs)
    give_bonus(api, event["id"], shadow["id"], points=12)
    assert total_points(api, shadow["id"]) == 12

    signup = sign_up(api)

    assert signup["already_exists"] is True
    assert signup["member"]["id"] == shadow["id"], "signing up minted a second row and stranded the points"
    assert total_points(api, shadow["id"]) == 12


def test_points_awarded_before_signup_survive_when_only_the_email_matches(api, seed_refs, attendable_action, outbound):
    """No uni_id on the shadow row - the email is the only thing linking them."""
    shadow = create_shadow_member(api, uni_id=None)
    event = create_event(api, seed_refs)
    give_bonus(api, event["id"], shadow["id"], points=12)

    signup = sign_up(api)

    assert signup["already_exists"] is True
    assert signup["member"]["id"] == shadow["id"]
    assert signup["member"]["uni_id"] == CLERK_UNI_ID, "the signup should backfill the uni_id it now knows"
    assert total_points(api, shadow["id"]) == 12


def test_signing_up_twice_is_idempotent(api, seed_refs, outbound):
    """The member app calls `POST /members/` on every sign-in, not just the first."""
    first = sign_up(api)
    second = sign_up(api)

    assert first["already_exists"] is False
    assert second["already_exists"] is True
    assert second["member"]["id"] == first["member"]["id"]
