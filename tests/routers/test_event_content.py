"""The event content links a pasted-URL flow manages.

Every route below shares the same guard as the meeting-url route
(``events.edit for event``); the published-only filtering on the
leaderboard-serving payloads is what keeps draft links from leaking, and the
database's unique index keeps two links of one event from sharing a name.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, func

from app.DB.schema import EventContentLinks, EventsContentLinkKind
from tests.factories import make_create_event_payload, make_event
from tests.utils import assert_2xx, assert_conflict, assert_forbidden, assert_not_found, assert_unprocessable


def make_link_payload(**overrides):
    payload = {"label": "Day 1 Slides", "url": "https://drive.google.com/file/d/abc123", "kind": "slides"}
    payload.update(overrides)
    return payload


def test_a_created_link_is_published_and_shows_up_in_the_detail_response(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    response = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload())
    assert_2xx(response)
    link = response.json()
    assert link["id"] and link["label"] == "Day 1 Slides"
    assert link["published"] is True, "a manually pasted link goes live at once"

    detail = admin_client.get(f"/events/{event['id']}").json()
    assert [l["id"] for l in detail["content_links"]] == [link["id"]]


def test_a_created_link_shows_up_in_the_public_events_list(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()

    listed = next(e for e in admin_client.get("/events").json() if e["id"] == event["id"])
    assert [l["id"] for l in listed["content_links"]] == [link["id"]]


def test_the_admin_details_response_carries_unpublished_rows_too(admin_client: TestClient):
    """The Content tab must still be able to see - and re-publish - a draft."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()
    admin_client.put(f"/events/{event['id']}/content-links/{link['id']}", json={"published": False})

    details = admin_client.get(f"/events/{event['id']}/details").json()
    assert [l["id"] for l in details["content_links"]] == [link["id"]]
    assert details["content_links"][0]["published"] is False
    # The public-facing payloads must not carry the unpublished row.
    assert admin_client.get(f"/events/{event['id']}").json()["content_links"] == []


def test_an_unpublished_link_is_hidden_from_the_events_list(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()
    admin_client.put(f"/events/{event['id']}/content-links/{link['id']}", json={"published": False})

    listed = next(e for e in admin_client.get("/events").json() if e["id"] == event["id"])
    assert listed["content_links"] == []


def test_an_event_without_links_returns_an_empty_array(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    assert admin_client.get(f"/events/{event['id']}").json()["content_links"] == []
    assert admin_client.get(f"/events/{event['id']}/details").json()["content_links"] == []


def test_the_my_events_route_never_gains_content_links(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    admin_client.put(f"/events/{event['id']}/status", json={"status": "active"})
    admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload())

    mine = admin_client.get("/events/me").json()
    for entry in mine["attended"] + mine["participated"]:
        if entry["id"] == event["id"]:
            assert "content_links" not in entry


def test_invalid_scheme_urls_are_rejected(admin_client: TestClient, db_session):
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    for url in ["ftp://example.com/slides.pdf", "javascript:alert(1)"]:
        response = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload(url=url))
        assert_unprocessable(response)


def test_a_bare_domain_is_normalized_into_an_https_url(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    response = admin_client.post(
        f"/events/{event['id']}/content-links", json=make_link_payload(url="youtu.be/dQw4w9WgXcQ")
    )
    assert_2xx(response)
    assert response.json()["url"] == "https://youtu.be/dQw4w9WgXcQ"


def test_an_url_over_500_characters_is_rejected(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    response = admin_client.post(
        f"/events/{event['id']}/content-links", json=make_link_payload(url="https://example.com/" + "a" * 500)
    )
    assert_unprocessable(response)


def test_a_blank_label_is_rejected(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    response = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload(label="   "))
    assert_unprocessable(response)


def test_a_label_over_200_characters_is_rejected(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    response = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload(label="x" * 201))
    assert_unprocessable(response)


def test_updating_the_label_alone_keeps_everything_else(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()

    response = admin_client.put(f"/events/{event['id']}/content-links/{link['id']}", json={"label": "Bootcamp Slides"})
    assert_2xx(response)
    updated = response.json()
    assert updated["label"] == "Bootcamp Slides"
    assert updated["url"] == link["url"] and updated["kind"] == "slides" and updated["published"] is True


def test_updating_the_url_alone(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()

    response = admin_client.put(
        f"/events/{event['id']}/content-links/{link['id']}", json={"url": "https://youtube.com/watch?v=abc"}
    )
    assert_2xx(response)
    updated = response.json()
    assert updated["url"] == "https://youtube.com/watch?v=abc"
    assert updated["label"] == link["label"] and updated["kind"] == "slides"


def test_updating_the_kind_alone_moves_the_row_to_recordings(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()

    response = admin_client.put(f"/events/{event['id']}/content-links/{link['id']}", json={"kind": "recording"})
    assert_2xx(response)
    assert response.json()["kind"] == "recording"


def test_toggling_publish_off_hides_the_link_till_it_is_toggled_back(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()
    url = f"/events/{event['id']}/content-links/{link['id']}"

    off = admin_client.put(url, json={"published": False}).json()
    assert off["published"] is False
    assert admin_client.get(f"/events/{event['id']}").json()["content_links"] == []

    on = admin_client.put(url, json={"published": True}).json()
    assert on["published"] is True
    assert [l["id"] for l in admin_client.get(f"/events/{event['id']}").json()["content_links"]] == [link["id"]]


def test_an_update_to_an_unknown_link_is_a_404(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    response = admin_client.put(
        "/events/{}/content-links/00000000-0000-0000-0000-000000000000".format(event["id"]), json={"label": "x"}
    )
    assert_not_found(response)


def test_a_link_id_from_another_event_cannot_be_edited(admin_client: TestClient):
    event_1 = admin_client.post("/events", json=make_create_event_payload()).json()
    event_2 = admin_client.post("/events", json=make_create_event_payload(event=make_event(name="other event"))).json()
    link = admin_client.post(f"/events/{event_1['id']}/content-links", json=make_link_payload()).json()

    response = admin_client.put(f"/events/{event_2['id']}/content-links/{link['id']}", json={"label": "hijack"})
    assert_not_found(response)


def test_a_deleted_link_is_gone_from_the_detail_response(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()

    response = admin_client.delete(f"/events/{event['id']}/content-links/{link['id']}")
    assert_2xx(response)
    assert admin_client.get(f"/events/{event['id']}").json()["content_links"] == []
    assert admin_client.get(f"/events/{event['id']}/details").json()["content_links"] == []


def test_deleting_an_unknown_link_is_a_404(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    response = admin_client.delete(f"/events/{event['id']}/content-links/00000000-0000-0000-0000-000000000000")
    assert_not_found(response)


def test_links_are_created_in_the_event_their_path_names(admin_client: TestClient):
    """The row is scoped by the event in the path, not by a body field."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()

    listed = next(e for e in admin_client.get("/events").json() if e["id"] == event["id"])
    assert [l["id"] for l in listed["content_links"]] == [link["id"]]


def test_the_planner_url_validator_rejects_every_unsafe_shape(admin_client: TestClient):
    """The render layer's first line of defense is this validator."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    for url in [
        "JavaScript:alert(1)",  # masked-case scheme without ";"
        "data:text/html;base64,PHN",
        "vbscript:msgbox(1)",
        "//drive.google.com/file/d/x",  # protocol-relative
        "user:pass@drive.google.com/x",  # credentials in a display link
        "example.com:8080/x",  # "host:" cannot be told from a scheme cheaply
        "https://drive.google.com/\x01",  # control characters anywhere
        "JavaScript://hit",  # scheme+";:" - rejected, not normalized
        "e" * 492 + ".com",  # grows past 500 once https:// is prepended
    ]:
        response = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload(url=url))
        assert response.status_code in (422, 400), (url, response.status_code, response.text)


def test_a_full_url_with_a_port_is_still_accepted(admin_client: TestClient):
    """The host:port rejection is for scheme-less paste accidents only."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    response = admin_client.post(
        f"/events/{event['id']}/content-links", json=make_link_payload(url="https://example.com:8080/x")
    )
    assert_2xx(response)
    assert response.json()["url"] == "https://example.com:8080/x"


def test_a_redirect_target_in_the_query_is_a_bare_domain(admin_client: TestClient):
    """The scheme is read from the start of the value: a pasted redirect
    target is a bare domain under ``example.com`` and gets the prefix."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    response = admin_client.post(
        f"/events/{event['id']}/content-links", json=make_link_payload(url="example.com/redirect?to=https://x.com")
    )
    assert_2xx(response)
    assert response.json()["url"] == "https://example.com/redirect?to=https://x.com"


def test_an_url_without_a_usable_host_is_rejected(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    for url in ["https://", "https://exa mple .com"]:
        response = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload(url=url))
        assert_unprocessable(response)


def test_an_uppercase_scheme_is_still_a_link(admin_client: TestClient):
    """Pasted "HTTPS://..." is an http(s) URL the same way; the public render
    guard reads schemes case-insensitively too."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    response = admin_client.post(
        f"/events/{event['id']}/content-links", json=make_link_payload(url="HTTPS://drive.google.com/file/d/x")
    )
    assert_2xx(response)
    assert response.json()["url"] == "HTTPS://drive.google.com/file/d/x"


def test_a_malformed_link_id_in_the_path_is_a_422(admin_client: TestClient):
    """link_id is typed UUID in the routes: garbage and non-ASCII bytes stop at
    the path converter instead of reaching MySQL as an illegal collation mix."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    for path in [f"/events/{event['id']}/content-links/not-a-uuid", f"/events/{event['id']}/content-links/%C3%A9"]:
        assert_unprocessable(admin_client.put(path, json={"label": "x"}))
        assert_unprocessable(admin_client.delete(path))


def test_the_url_length_limit_applies_after_normalization(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    too_long = admin_client.post(
        f"/events/{event['id']}/content-links", json=make_link_payload(url="https://example.com/" + "a" * 492)
    )
    assert_unprocessable(too_long)

    # A 492-char bare domain fits exactly once the scheme is added.
    ok = admin_client.post(
        f"/events/{event['id']}/content-links", json=make_link_payload(url="example.com/" + "a" * 477)
    )
    assert_2xx(ok)
    assert ok.json()["url"] == "https://example.com/" + "a" * 477


def test_an_explicit_null_in_the_update_is_rejected(admin_client: TestClient):
    """A row is never cleared: null is not "unset this field"."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()
    url = f"/events/{event['id']}/content-links/{link['id']}"
    other = make_link_payload(label="Day 1 Recording", url="https://drive.google.com/file/d/rec", kind="recording")
    admin_client.post(f"/events/{event['id']}/content-links", json=other)

    for payload in [{"url": None}, {"label": None}, {"kind": None}, {"published": None}, {**other, "kind": None}]:
        response = admin_client.put(url, json=payload)
        assert_unprocessable(response)
        detail = admin_client.get(f"/events/{event['id']}").json()
        first = next(l for l in detail["content_links"] if l["id"] == link["id"])
        assert first["label"] == link["label"] and first["url"] == link["url"], payload


def test_two_links_with_the_same_label_cannot_be_inserted_even_secretly(
    admin_client: TestClient, db_session, seed_refs
):
    """The unique index is the race backstop. The API's pre-check gives the
    friendly 409; a write that slips past it still cannot commit."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()
    assert [l["id"] for l in admin_client.get(f"/events/{event['id']}").json()["content_links"]] == [link["id"]]

    db_session.add(
        EventContentLinks(
            event_id=event["id"],
            label="day 1 slides",
            url="https://example.com/slides",
            kind=EventsContentLinkKind.SLIDES,
        )
    )
    # The route's commit lives inside the outer test transaction; flushing the
    # duplicate against the same transaction is exactly what two racing
    # writers would trigger - and the index refuses it.
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_two_orm_rows_with_the_same_label_in_one_event_are_a_409(admin_client: TestClient, db_session):
    """Case-insensitive: the utf8mb4 ai_ci collation sees DAY 1 == day 1."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload())

    row_count = db_session.scalar(
        select(func.count())
        .select_from(EventContentLinks)
        .where(EventContentLinks.event_id == event["id"], EventContentLinks.label == "DAY 1 SLIDES")
    )
    assert row_count == 1
    duplicate = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload(label="DAY 1 SLIDES"))
    assert_conflict(duplicate)


def test_deleting_the_event_deletes_its_links(admin_client: TestClient, db_session):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload())

    response = admin_client.delete(f"/events/{event['id']}")
    assert_2xx(response)
    count = db_session.scalar(
        select(func.count()).select_from(EventContentLinks).where(EventContentLinks.event_id == event["id"])
    )
    assert count == 0, "the ON DELETE CASCADE took the links with the event"


def test_an_events_edit_holder_of_another_department_is_refused(club, seed_refs):
    """A real roster, not an access double: an events.edit leader of Robotics
    cannot manage the content links of an AI event."""
    from app.services.permissions.catalogue import Perm

    robotics, ai = club.department("Robotics"), club.department("AI")
    club.department_permission(robotics, Perm.EVENTS_EDIT)
    outsider = club.join(club.person(), robotics, "leader")
    event_id = club.event(ai, seed_refs)

    outsider_client = club.sign_in(outsider)
    assert_forbidden(outsider_client.post(f"/events/{event_id}/content-links", json=make_link_payload()))
    assert_forbidden(
        outsider_client.put(
            f"/events/{event_id}/content-links/00000000-0000-0000-0000-000000000000", json={"label": "x"}
        )
    )
    assert_forbidden(outsider_client.delete(f"/events/{event_id}/content-links/00000000-0000-0000-0000-000000000000"))


def test_a_second_link_with_the_same_label_is_a_409(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload())

    duplicate = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload())
    assert_conflict(duplicate)


def test_label_uniqueness_is_case_insensitive(admin_client: TestClient):

    event = admin_client.post("/events", json=make_create_event_payload()).json()
    admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload(label="Day 1 Slides"))

    duplicate = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload(label="day 1 slides"))
    assert_conflict(duplicate)


def test_label_uniqueness_is_accent_insensitive(admin_client: TestClient):
    """The ai_ci collation folds accents the same way the friendly pre-check
    now does - both take the plain ``=`` path, never the exception."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload(label="Résumé"))

    duplicate = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload(label="Resume"))
    assert_conflict(duplicate)


def test_the_details_shape_puts_drafts_at_the_top_if_the_admin_asks(admin_client: TestClient):
    """Unpublished rows live in the top-level list; the nested event object
    never grows them - pinning /details to stay that way."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()
    admin_client.put(f"/events/{event['id']}/content-links/{link['id']}", json={"published": False})

    details = admin_client.get(f"/events/{event['id']}/details").json()
    assert [l["id"] for l in details["content_links"]] == [link["id"]]
    assert details["content_links"][0]["published"] is False
    assert "content_links" not in details["event"]


def test_the_same_label_is_fine_on_another_event(admin_client: TestClient):
    event_1 = admin_client.post("/events", json=make_create_event_payload()).json()
    event_2 = admin_client.post("/events", json=make_create_event_payload(event=make_event(name="other event"))).json()
    admin_client.post(f"/events/{event_1['id']}/content-links", json=make_link_payload())

    response = admin_client.post(f"/events/{event_2['id']}/content-links", json=make_link_payload())
    assert_2xx(response)


def test_renaming_a_link_onto_an_existing_label_is_a_409(admin_client: TestClient):

    event = admin_client.post("/events", json=make_create_event_payload()).json()
    first = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()
    second = admin_client.post(
        f"/events/{event['id']}/content-links",
        json=make_link_payload(label="Day 1 Recording", url="https://drive.google.com/file/d/rec", kind="recording"),
    ).json()

    response = admin_client.put(f"/events/{event['id']}/content-links/{second['id']}", json={"label": first["label"]})
    assert_conflict(response)
    # Nothing moved: the row keeps its label.
    assert admin_client.get(f"/events/{event['id']}/details").json()["content_links"][1]["label"] == "Day 1 Recording"


def test_a_link_keeping_its_own_label_still_updates(admin_client: TestClient):

    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()

    keep = admin_client.put(
        f"/events/{event['id']}/content-links/{link['id']}",
        json={"label": "day 1 slides", "url": "https://drive.google.com/file/d/other"},
    )
    assert_2xx(keep)
    assert keep.json()["url"] == "https://drive.google.com/file/d/other"


def test_a_label_freed_by_a_delete_can_be_used_again(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()
    admin_client.delete(f"/events/{event['id']}/content-links/{link['id']}")

    response = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload())
    assert_2xx(response)


def test_non_staff_cannot_manage_content_links(clerk_client: TestClient):
    """Held nowhere → refused before the event is even looked up (guards.py)."""
    assert_forbidden(clerk_client.post("/events/9999/content-links", json=make_link_payload()))
    assert_forbidden(
        clerk_client.put("/events/9999/content-links/00000000-0000-0000-0000-000000000000", json={"label": "x"})
    )
    assert_forbidden(clerk_client.delete("/events/9999/content-links/00000000-0000-0000-0000-000000000000"))


def test_managing_links_on_an_unknown_event_is_a_404(admin_client: TestClient):
    assert_not_found(admin_client.post("/events/9999/content-links", json=make_link_payload()))
    assert_not_found(
        admin_client.put("/events/9999/content-links/00000000-0000-0000-0000-000000000000", json={"label": "x"})
    )
    assert_not_found(admin_client.delete("/events/9999/content-links/00000000-0000-0000-0000-000000000000"))


def test_creating_a_link_records_who_created_it(admin_client: TestClient, db_session):
    from sqlalchemy import select

    from app.DB.schema import EventContentLinks

    event = admin_client.post("/events", json=make_create_event_payload()).json()
    admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload())
    me = admin_client.get("/members/me").json()

    link = db_session.scalars(select(EventContentLinks)).first()
    assert link is not None and link.created_by == me["id"]


def test_the_content_link_mutations_record_event_history(admin_client: TestClient):
    """Publish, repoint or delete a public link and the event's history shows
    it - who did it and what the row looked like before and after."""
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    me = admin_client.get("/members/me").json()
    link = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload()).json()
    url = f"/events/{event['id']}/content-links/{link['id']}"

    history = admin_client.get(f"/events/{event['id']}/history").json()["items"]
    created = history[-1]
    assert created["action"] == "content_link_changed"
    assert created["actor"] and created["actor"]["member_id"] == me["id"]
    assert created["details"]["content_link"][0] is None
    assert created["details"]["content_link"][1]["label"] == "Day 1 Slides"
    assert created["details"]["content_link"][1]["published"] is True

    admin_client.put(url, json={"label": "Bootcamp Slides", "published": False})
    history = admin_client.get(f"/events/{event['id']}/history").json()["items"]
    updated = history[-1]
    assert updated["details"]["content_link"][0]["label"] == "Day 1 Slides"
    assert updated["details"]["content_link"][0]["published"] is True
    assert updated["details"]["content_link"][1]["label"] == "Bootcamp Slides"
    assert updated["details"]["content_link"][1]["published"] is False

    admin_client.delete(url)
    history = admin_client.get(f"/events/{event['id']}/history").json()["items"]
    deleted = history[-1]
    assert deleted["details"]["content_link"][0]["label"] == "Bootcamp Slides"
    assert deleted["details"]["content_link"][1] is None


def test_a_failed_cache_reset_is_logged_and_never_fails_the_write(admin_client: TestClient, monkeypatch, caplog):
    """Outages on the leaderboard app must not 500 the user - and they must
    leave a trail, or a publish toggle would silently not reach members."""
    import logging

    from app import leaderboard_cache

    def offline_reset():
        raise RuntimeError("leaderboard offline")

    monkeypatch.setattr(leaderboard_cache, "reset_leaderboard_cache", offline_reset)
    event = admin_client.post("/events", json=make_create_event_payload()).json()

    with caplog.at_level(logging.ERROR, logger="app.leaderboard_cache"):
        response = admin_client.post(f"/events/{event['id']}/content-links", json=make_link_payload())
    assert_2xx(response)
    assert "leaderboard offline" in caplog.text


def test_the_duplicate_label_matcher_fires_only_on_1062_and_our_index():
    """1062 alone is not enough - the PK collides too. FK codes must pass
    through as something other than a label clash."""
    from app.routers import events as events_router

    ours = IntegrityError(
        "INSERT INTO event_content_links",
        {},
        RuntimeError(
            1062, "Duplicate entry '1-day 1' for key 'event_content_links.uq_event_content_links_event_label'"
        ),
    )
    assert events_router._duplicate_label_error(ours) is True

    primary_key = IntegrityError(
        "INSERT INTO event_content_links", {}, RuntimeError(1062, "Duplicate entry 'x' for key 'PRIMARY'")
    )
    assert events_router._duplicate_label_error(primary_key) is False

    foreign_key = IntegrityError(
        "INSERT INTO event_content_links",
        {},
        RuntimeError(1452, "Cannot add or update a child row: a foreign key constraint fails"),
    )
    assert events_router._duplicate_label_error(foreign_key) is False
