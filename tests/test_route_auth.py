"""An inventory of the guard every route enforces.

This is a security regression test, not a style check. Phase 3 of the refactor
moved guards from route parameters into decorators, and four `/emails` routes
silently dropped from `admin_guard` to `authenticated_guard` in the process -
the existing tests could not catch it, because they override the guards.

If a route's auth changes, this test fails and the diff shows exactly which
route and in which direction. Update the mapping deliberately, never reflexively.
"""

import pytest
from fastapi.routing import APIRoute

from app.main import app

GUARD_NAMES = {"admin_guard", "authenticated_guard", "super_admin_guard", "admin_points_guard", "optional_clerk_guard"}
STRICTNESS = {"super_admin_guard": 3, "admin_guard": 2, "authenticated_guard": 1, "optional_clerk_guard": 0}

# route -> the strictest guard it enforces, or None when the route is public
EXPECTED_AUTH: dict[str, str | None] = {
    'DELETE /actions/{action_id:int}': None,  # FIXME: unauthenticated write endpoint (pre-existing)
    'DELETE /attendance/{event_id}/manual': 'admin_guard',
    'DELETE /custom/departments/{log_id}': 'admin_guard',
    'DELETE /custom/members/{log_id}': 'admin_guard',
    'DELETE /emails/blast/templates/{template_id:int}': 'admin_guard',
    'DELETE /events/{event_id:int}': 'admin_guard',
    'DELETE /semesters/{semester_id:int}': 'super_admin_guard',
    'GET /': None,
    'GET /actions': None,
    'GET /actions/all': None,
    'GET /attendance/{event_id:int}': 'optional_clerk_guard',
    'GET /custom/departments/{event_id}': 'admin_guard',
    'GET /custom/members/{event_id}': 'admin_guard',
    'GET /departments': None,
    'GET /departments/{department_id:int}': None,
    'GET /emails/blast/eligible-count': 'admin_guard',
    'GET /emails/blast/templates': 'admin_guard',
    'GET /emails/certificate-event/eligible-count/{event_id:int}': 'admin_guard',
    'GET /emails/certificate-event/logs/stream/{event_id:int}': 'admin_guard',
    'GET /emails/logs': 'admin_guard',
    'GET /emails/logs/enriched': 'admin_guard',
    'GET /emails/logs/enriched/stream': 'admin_guard',
    'GET /emails/logs/event/{event_id:int}': 'admin_guard',
    'GET /emails/logs/member/{member_id:int}': 'admin_guard',
    'GET /emails/stats': 'admin_guard',
    'GET /emails/stats/dashboard': 'admin_guard',
    'GET /events/': None,
    'GET /events/me': 'authenticated_guard',
    'GET /events/open': None,
    'GET /events/submissions/{event_id:int}': 'admin_guard',
    'GET /events/{event_id:int}': None,
    'GET /events/{event_id:int}/details': 'admin_guard',
    'GET /events/{event_id:int}/form': None,
    'GET /forms/': None,
    'GET /forms/{form_id:int}': None,
    'GET /health': None,
    'GET /health/db': None,
    'GET /health/print-status': None,
    'GET /members/': 'admin_guard',
    'GET /members/me': 'authenticated_guard',
    'GET /members/roles': 'super_admin_guard',
    'GET /members/uni-id/{uni_id}': 'admin_guard',
    'GET /members/{member_id:int}': 'admin_guard',
    'GET /points/departments/total': None,
    'GET /points/departments/{department_id:int}': None,
    'GET /points/members/total': None,
    'GET /points/members/{member_id:int}': None,
    'GET /points/semesters': None,
    'GET /semesters': 'admin_guard',
    'GET /submissions/test-google-forms/{google_form_id}': None,
    'GET /submissions/{form_id:int}': 'authenticated_guard',
    'GET /wallet/health': None,
    'GET /wallet/me': 'authenticated_guard',
    'GET /wallet/{uuid}': None,
    'PATCH /members/me': 'authenticated_guard',
    'PATCH /wallet/me': 'authenticated_guard',
    'POST /actions': None,  # FIXME: unauthenticated write endpoint (pre-existing)
    'POST /attendance/{event_id:int}': 'authenticated_guard',
    'POST /attendance/{event_id}/backfill': 'admin_guard',
    'POST /attendance/{event_id}/manual': 'admin_guard',
    'POST /cache/reset': 'admin_guard',
    'POST /custom/departments': 'admin_guard',
    'POST /custom/members': 'admin_guard',
    'POST /emails/acceptance/blasts/{event_id:int}': 'admin_guard',
    'POST /emails/acceptance/test': 'admin_guard',
    'POST /emails/blast': 'admin_guard',
    'POST /emails/blast/templates': 'admin_guard',
    'POST /emails/blast/test': 'admin_guard',
    'POST /emails/custom/{event_id:int}': 'admin_guard',
    'POST /emails/custom/{event_id:int}/test': 'admin_guard',
    'POST /emails/direct': 'admin_guard',
    'POST /emails/download-certificate/{event_id:int}': 'authenticated_guard',
    'POST /emails/manual-certificate': 'admin_guard',
    'POST /emails/{event_id:int}': 'admin_guard',
    'POST /events/': 'admin_guard',
    'POST /members/': 'authenticated_guard',
    'POST /members/batch': 'super_admin_guard',
    'POST /members/manual': 'super_admin_guard',
    'POST /members/roles': 'super_admin_guard',
    'POST /semesters': 'super_admin_guard',
    'POST /submissions/google/webhook': None,
    'POST /submissions/{form_id:int}': None,
    'POST /submissions_manual/google/run/{google_form_id}': None,  # FIXME: unauthenticated write endpoint (pre-existing)
    'POST /submissions_manual/google/{google_form_id}': None,  # FIXME: unauthenticated write endpoint (pre-existing)
    'POST /upload/': 'admin_guard',
    'POST /upload/email-attachment': 'admin_guard',
    'POST /wallet/apple-pass': None,
    'POST /wallet/google-pass': None,
    'PUT /actions/reorder': None,  # FIXME: unauthenticated write endpoint (pre-existing)
    'PUT /actions/{action_id:int}': None,  # FIXME: unauthenticated write endpoint (pre-existing)
    'PUT /custom/departments/{log_id}': 'admin_guard',
    'PUT /custom/members/{log_id}': 'admin_guard',
    'PUT /emails/blast/templates/{template_id:int}': 'admin_guard',
    'PUT /events/{event_id:int}': 'admin_guard',
    'PUT /events/{event_id:int}/status': 'admin_guard',
    'PUT /forms/{form_id:int}': 'admin_guard',
    'PUT /semesters/{semester_id:int}': 'super_admin_guard',
    'PUT /semesters/{semester_id:int}/current': 'super_admin_guard',
    'PUT /submissions/accept': 'admin_guard',
    'PUT /wallet/me': 'authenticated_guard',
}


def _guards(dependant) -> set[str]:
    found = set()
    call = getattr(dependant, "call", None)
    name = getattr(call, "__name__", None)
    if name in GUARD_NAMES:
        found.add(name)
    for sub in dependant.dependencies:
        found |= _guards(sub)
    return found


def _strictest(guards: set[str]) -> str | None:
    return max(guards, key=lambda g: STRICTNESS[g], default=None)


def actual_auth() -> dict[str, str | None]:
    out = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods):
                out[f"{method} {route.path}"] = _strictest(_guards(route.dependant))
    return out


def test_every_route_is_in_the_inventory():
    assert sorted(actual_auth()) == sorted(EXPECTED_AUTH), "a route was added or removed - update EXPECTED_AUTH"


@pytest.mark.parametrize("route", sorted(EXPECTED_AUTH))
def test_route_enforces_its_expected_guard(route):
    assert actual_auth()[route] == EXPECTED_AUTH[route]


def test_no_route_silently_becomes_public():
    public = {r for r, g in actual_auth().items() if g is None}
    assert public == {r for r, g in EXPECTED_AUTH.items() if g is None}
