"""``GET /club-structure/members/{id}``: a member as staff see them in the app, through the real guards and rosters."""

from datetime import datetime

import pytest

from app.DB.schema import MemberProfiles, SemesterDepartments

LINK = {"id": "l1", "platform": "linkedin", "url": "https://www.linkedin.com/in/someone", "label": None}


@pytest.fixture
def staff(club):
    """A plain roster member of a department: staff, so they hold ``club_structure.view``."""
    viewer = club.join(club.person(), club.department("Viewers"))
    club.sign_in(viewer)
    return viewer


def profile_for(session, member, *, visibility=None, **fields) -> MemberProfiles:
    values = {
        "uuid": f"card{member.id:028d}",
        "major": "Computer Science",
        "institution": "Qassim University",
        "bio": "Hello",
        "social_links": [LINK],
        "visibility": visibility,
        **fields,
    }
    profile = MemberProfiles(member_id=member.id, created_at=datetime(2026, 7, 1), **values)
    session.add(profile)
    session.flush()
    return profile


def target(club, **member_fields):
    person = club.person()
    for key, value in {"phone_number": "0500000000", "uni_level": 5, "uni_college": "CS", **member_fields}.items():
        setattr(person, key, value)
    club.session.flush()
    return person


def view(club, member_id):
    """Signed in as ``staff``. Commits first, like ``Club.sign_in``, so the route's own session sees the rows."""
    club.session.commit()
    response = club.client.get(f"/club-structure/members/{member_id}")
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------- access


def test_anonymous_callers_are_refused(client, club):
    person = club.person()
    club.session.commit()
    assert client.get(f"/club-structure/members/{person.id}").status_code == 403


def test_a_member_who_is_not_staff_is_refused(club):
    outsider, person = club.person(), club.person()
    assert club.sign_in(outsider).get(f"/club-structure/members/{person.id}").status_code == 403


def test_an_unknown_member_is_a_404(club, staff):
    assert club.client.get("/club-structure/members/999999").status_code == 404


def test_a_member_who_deleted_their_account_is_a_404(club, staff):
    person = club.person()
    person.deleted_at = datetime(2026, 7, 10)
    club.session.commit()
    assert club.client.get(f"/club-structure/members/{person.id}").status_code == 404


# ---------------------------------------------------------------- privacy


def test_everything_the_card_shows_is_sent(club, staff):
    person = target(club)
    profile_for(
        club.session, person, visibility={"showPhone": True, "showEmail": True, "showAcademic": True, "showBio": True}
    )
    body = view(club, person.id)
    assert body["member"] == {
        "id": person.id,
        "name": person.name,
        "public_name": person.public_name,
        "gender": "Male",
        "uni_level": 5,
        "uni_college": "CS",
    }
    assert body["contact"] == {"email": person.email, "phone_number": "0500000000"}
    assert body["profile"] == {
        "uuid": f"card{person.id:028d}",
        "major": "Computer Science",
        "institution": "Qassim University",
        "bio": "Hello",
        "social_links": [LINK],
    }


def test_each_switch_hides_its_own_part(club, staff):
    person = target(club)
    profile_for(
        club.session,
        person,
        visibility={"showPhone": False, "showEmail": False, "showAcademic": False, "showBio": False},
    )
    body = view(club, person.id)
    assert (body["member"]["uni_level"], body["member"]["uni_college"]) == (None, None)
    assert body["contact"] == {"email": None, "phone_number": None}
    assert body["profile"] == {
        "uuid": f"card{person.id:028d}",
        "major": None,
        "institution": None,
        "bio": None,
        "social_links": [LINK],
    }


@pytest.mark.parametrize(
    "switch,shown",
    [
        ("showEmail", lambda b: b["contact"]["email"]),
        ("showPhone", lambda b: b["contact"]["phone_number"]),
        ("showBio", lambda b: b["profile"]["bio"]),
        ("showAcademic", lambda b: b["profile"]["major"]),
    ],
)
def test_one_switch_on_shows_only_its_part(club, staff, switch, shown):
    person = target(club)
    off = {"showPhone": False, "showEmail": False, "showAcademic": False, "showBio": False}
    profile_for(club.session, person, visibility={**off, switch: True})
    body = view(club, person.id)
    assert shown(body) is not None
    visible = [
        body["contact"]["email"],
        body["contact"]["phone_number"],
        body["profile"]["bio"],
        body["profile"]["major"],
        body["profile"]["institution"],
        body["member"]["uni_level"],
        body["member"]["uni_college"],
    ]
    assert sum(value is not None for value in visible) == (3 if switch == "showAcademic" else 1)


def test_switches_never_saved_read_as_the_public_cards_defaults(club, staff):
    # As GET /wallet/{uuid}: phone and e-mail hidden, academic details and bio shown.
    person = target(club)
    profile_for(club.session, person, visibility={"academicConfigured": True})
    body = view(club, person.id)
    assert body["contact"] == {"email": None, "phone_number": None}
    assert (body["profile"]["major"], body["profile"]["bio"], body["member"]["uni_level"]) == (
        "Computer Science",
        "Hello",
        5,
    )


def test_a_member_without_a_card_gets_the_empty_profile(club, staff):
    person = target(club)
    body = view(club, person.id)
    assert body["profile"] == {"uuid": None, "major": None, "institution": None, "bio": None, "social_links": []}
    assert body["contact"] == {"email": None, "phone_number": None}


def test_social_links_are_always_sent_and_malformed_ones_left_out(club, staff):
    person = target(club)
    links = [LINK, {"platform": "github", "url": "https://github.com/x"}, {"platform": "x"}, "junk"]
    profile_for(club.session, person, social_links=links, visibility={"showAcademic": False, "showBio": False})
    assert view(club, person.id)["profile"]["social_links"] == [
        LINK,
        {"id": "1", "platform": "github", "url": "https://github.com/x", "label": None},
    ]


def test_empty_strings_are_null(club, staff):
    person = target(club, phone_number="", uni_college="")
    profile_for(
        club.session,
        person,
        major="",
        institution="",
        bio="",
        visibility={"showPhone": True, "showEmail": True, "showAcademic": True, "showBio": True},
    )
    body = view(club, person.id)
    assert body["contact"]["phone_number"] is None
    assert body["member"]["uni_college"] is None
    assert (body["profile"]["major"], body["profile"]["institution"], body["profile"]["bio"]) == (None, None, None)


# ---------------------------------------------------------------- roles


def test_roles_are_this_semesters_departments_and_roles(club, staff):
    design, media, old = club.department("Design"), club.department("Media"), club.department("Old")
    person = target(club)
    club.join(person, design, "leader")
    club.join(person, media)
    club.join(person, old, "vp", semester=club.previous)
    scope = club.session.get(SemesterDepartments, (club.current.id, media.id))
    scope.name, scope.ar_name = "Media 475", "الإعلام 475"
    body = view(club, person.id)
    assert body["semester"] == {"id": club.current.id, "name": club.current.name}
    assert body["roles"] == [
        {"department_id": design.id, "name": "Design", "ar_name": "قسم Design", "roles": ["leader", "member"]},
        {"department_id": media.id, "name": "Media 475", "ar_name": "الإعلام 475", "roles": ["member"]},
    ]


def test_a_member_who_is_not_staff_now_has_no_roles(club, staff):
    person = target(club)
    club.join(person, club.department("Past"), "leader", semester=club.previous)
    body = view(club, person.id)
    assert body["roles"] == []
    assert body["semester"]["id"] == club.current.id
