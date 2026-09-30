"""An inventory of the guard every route enforces.

This is a security regression test, not a style check. Phase 3 of the refactor
moved guards from route parameters into decorators, and four `/emails` routes
silently dropped from `admin_guard` to `authenticated_guard` in the process -
the existing tests could not catch it, because they override the guards.

If a route's auth changes, this test fails and the diff shows exactly which
route and in which direction. Update the mapping deliberately, never reflexively.

The six endpoints that previously had no auth at all - the `/actions` writes and
both `/submissions_manual/google/*` routes - are now guarded. `/actions` reads
stay public: the leaderboard app consumes them.

Five routes in `/points` and `/submissions` used to depend on the Clerk bearer
directly rather than on a named guard. They were recorded here as public, which
was wrong - they did require a token. They now go through
`optional_clerk_guard` / `authenticated_guard` like everything else, so this
table describes them accurately and `dependency_overrides` can reach them.

The two `/wallet/*-pass` routes moved from `None` to `optional_clerk_guard` for
the same reason. They always read the caller's token - by pulling the
Authorization header off the request and calling the guard by hand, inside a
`try/except Exception` - which this test could not see. The token is a
dependency now, so the table describes what they actually do. Neither route
rejects an anonymous caller; both still issue a guest card.
"""

import pytest
from fastapi.routing import APIRoute

from app.main import app

# Clerk's own guards, named as they are. Everything stricter is a permission guard
# (app/services/permissions/guards.py) and is named by its ``auth_label``: a
# permission key such as "events.edit", "events.edit for event" when it is checked
# against the department of the event in the path, or "super_admin".
CLERK_GUARDS = {"authenticated_guard", "optional_clerk_guard"}


def _guards(dependant) -> set[str]:
    found = set()
    call = getattr(dependant, "call", None)
    label = getattr(call, "auth_label", None)
    if label:
        found.add(label)
    elif getattr(call, "__name__", None) in CLERK_GUARDS:
        found.add(call.__name__)
    for sub in dependant.dependencies:
        found |= _guards(sub)
    return found


def _strictness(label: str) -> int:
    if label == "super_admin":
        return 4
    if label not in CLERK_GUARDS:
        # The staff baseline (admin.access) is weaker than any specific permission on the same route.
        return 2 if label == "admin.access" else 3
    return 1 if label == "authenticated_guard" else 0


def _strictest(guards: set[str]) -> str | None:
    return max(guards, key=_strictness, default=None)


# route -> the strictest guard it enforces (a permission label, a Clerk guard, or None when public)
EXPECTED_AUTH: dict[str, str | None] = {
    "GET /club-structure/public": "optional_clerk_guard",
    "GET /club-structure": "club_structure.view",
    "GET /club-structure/roles": "club_structure.view",
    "GET /club-structure/departments/{department_id:int}": "club_structure.view",
    "GET /club-structure/departments/{department_id:int}/roster": "club_structure.view",
    "GET /club-structure/history": "club_structure.view",
    "POST /club-structure/departments": "club_structure.manage",
    "PUT /club-structure/departments/{department_id:int}": "club_structure.manage",
    "POST /club-structure/departments/{department_id:int}/archive": "club_structure.manage",
    "POST /club-structure/departments/{department_id:int}/restore": "club_structure.manage",
    "POST /club-structure/semesters/{semester_id}/departments/{department_id:int}": "club_structure.manage",
    "DELETE /club-structure/semesters/{semester_id}/departments/{department_id:int}": "club_structure.manage",
    "POST /club-structure/semesters/{semester_id}/departments/{department_id:int}/members": "club_structure.manage_roster for department",
    "DELETE /club-structure/semesters/{semester_id}/departments/{department_id:int}/members/{member_id:int}": "club_structure.manage_roster for department",
    "PUT /club-structure/semesters/{semester_id}/departments/{department_id:int}/members/{member_id:int}/roles/{role_key}": "club_structure.manage_roster for department",
    "DELETE /club-structure/semesters/{semester_id}/departments/{department_id:int}/members/{member_id:int}/roles/{role_key}": "club_structure.manage_roster for department",
    "POST /club-structure/semesters/{semester_id}/copy-from/{source_semester_id}": "club_structure.manage",
    "DELETE /actions/{action_id:int}": "points.catalogue",
    "DELETE /attendance/{event_id}/manual": "attendance.take for event",
    "DELETE /custom/departments/{log_id}": "points.custom",
    "DELETE /custom/members/{log_id}": "points.custom",
    "DELETE /emails/blast/templates/{template_id:int}": "emails.blast",
    "DELETE /events/{event_id:int}": "events.delete for event",
    "DELETE /semesters/{semester_id}": "semesters.manage",
    "GET /": None,
    "GET /actions": None,
    "GET /actions/all": None,
    "GET /attendance/{event_id:int}": "optional_clerk_guard",
    "GET /custom/departments/{event_id}": "points.custom",
    "GET /custom/members/{event_id}": "points.custom",
    "GET /departments": None,
    "GET /departments/{department_id:int}": None,
    "GET /emails/blast/eligible-count": "emails.blast",
    "GET /emails/blast/templates": "emails.blast",
    "GET /emails/certificate-event/eligible-count/{event_id:int}": "emails.event for event",
    "GET /emails/certificate-event/logs/stream/{event_id:int}": "emails.event for event",
    "GET /emails/jobs": "emails.logs",
    "GET /emails/jobs/unfinished": "admin.access",
    "GET /emails/jobs/{job_id:int}": "admin.access",
    "GET /emails/logs": "emails.logs",
    "GET /emails/logs/enriched": "emails.logs",
    "GET /emails/logs/enriched/stream": "emails.logs",
    "GET /emails/logs/event/{event_id:int}": "emails.event for event",
    "GET /emails/logs/member/{member_id:int}": "emails.logs",
    "GET /emails/stats": "emails.logs",
    "GET /emails/stats/dashboard": "admin.access",
    "GET /events/": None,
    "GET /events/me": "authenticated_guard",
    "GET /events/paginated": "events.view",
    "GET /events/open": None,
    "GET /events/submissions/{event_id:int}": "submissions.review for event",
    "GET /events/{event_id:int}": None,
    "GET /events/{event_id:int}/details": "events.view",
    "GET /events/{event_id:int}/form": None,
    "GET /forms/": None,
    "GET /forms/{form_id:int}": None,
    "GET /forms/{form_id:int}/schema": "submissions.review for form",
    "GET /health": None,
    "GET /health/db": None,
    "GET /health/sentry": None,
    # Public for the same reason as /health/sentry: an external uptime check
    # has to reach it without a token, and that is the entire point of it -
    # the club Google token expires every seven days (docs/GOOGLE_FORMS.md)
    # and nothing else notices. It reveals that the integration is down, not
    # any credential.
    "GET /health/google": None,
    "GET /health/print-status": None,
    "GET /members/": "members.view",
    "GET /members/me": "authenticated_guard",
    "GET /members/paginated": "members.view",
    "GET /members/roles": "super_admin",
    "GET /members/stats": "members.view",
    "GET /members/uni-id/{uni_id}": "members.view",
    "GET /members/{member_id:int}": "members.view",
    "GET /points/departments/total": "optional_clerk_guard",
    "GET /points/departments/{department_id:int}": "optional_clerk_guard",
    "GET /points/members/total": "optional_clerk_guard",
    "GET /points/members/{member_id:int}": "optional_clerk_guard",
    "GET /points/semesters": None,
    "GET /semesters": "admin.access",
    "GET /submissions/sync-jobs/{job_id:int}": "admin.access",
    "GET /submissions/test-google-forms/{google_form_id}": None,
    "GET /submissions/{form_id:int}": "authenticated_guard",
    "GET /wallet/health": None,
    "GET /wallet/me": "authenticated_guard",
    "GET /wallet/{uuid}": None,
    "PATCH /members/me": "authenticated_guard",
    "PATCH /wallet/me": "authenticated_guard",
    "POST /actions": "points.catalogue",
    "POST /attendance/{event_id:int}": "authenticated_guard",
    "POST /attendance/{event_id}/backfill": "attendance.backfill for event",
    "POST /attendance/{event_id}/manual": "attendance.take for event",
    "POST /attendance/{event_id}/scan": "attendance.take for event",
    "POST /cache/reset": "cache.reset",
    "POST /custom/departments": "points.custom",
    "POST /custom/members": "points.custom",
    "POST /emails/acceptance/blasts/{event_id:int}": "emails.event for event",
    "POST /emails/acceptance/test": "emails.event",
    "POST /emails/blast": "emails.blast",
    "POST /emails/blast/templates": "emails.blast",
    "POST /emails/blast/test": "emails.blast",
    "POST /emails/custom/{event_id:int}": "emails.event for event",
    "POST /emails/custom/{event_id:int}/test": "emails.event for event",
    "POST /emails/direct": "emails.direct",
    "POST /emails/download-certificate/{event_id:int}": "authenticated_guard",
    "POST /emails/manual-certificate": "certificates.manual",
    "POST /emails/{event_id:int}": "emails.event for event",
    "POST /events/": "events.create",
    "POST /forms/{event_id:int}/attach": "forms.manage for event",
    "POST /forms/{event_id:int}/unattach": "forms.manage for event",
    "POST /forms/watches/renew": "forms.admin",
    "POST /members/": "authenticated_guard",
    "POST /members/batch": "members.create",
    "POST /members/manual": "members.create",
    "POST /members/roles": "super_admin",
    "POST /semesters": "semesters.manage",
    "POST /submissions/google/webhook": None,
    "POST /submissions/{form_id:int}": "authenticated_guard",
    "POST /submissions_manual/google/run/{google_form_id}": "forms.admin",
    "POST /submissions_manual/google/{google_form_id}": "forms.admin",
    "POST /upload/": "uploads",
    "POST /upload/email-attachment": "uploads",
    "POST /wallet/apple-pass": "optional_clerk_guard",
    "POST /wallet/google-pass": "optional_clerk_guard",
    "PUT /actions/reorder": "points.catalogue",
    "PUT /actions/{action_id:int}": "points.catalogue",
    "PUT /custom/departments/{log_id}": "points.custom",
    "PUT /custom/members/{log_id}": "points.custom",
    "PUT /emails/blast/templates/{template_id:int}": "emails.blast",
    "PUT /events/{event_id:int}": "events.edit for event",
    "PUT /events/{event_id:int}/meeting-url": "events.edit for event",
    "PUT /events/{event_id:int}/status": "events.edit for event",
    "PUT /forms/{form_id:int}": "forms.manage for form",
    "PUT /semesters/{semester_id}": "semesters.manage",
    "PUT /submissions/accept": "submissions.review",
    "PUT /wallet/me": "authenticated_guard",
    # Answers "not staff" for anyone signed in who is not; see app/services/permissions.
    "GET /access/me": "authenticated_guard",
    "GET /access/events/{event_id:int}": "admin.access",
    # Events pipeline: staff at the door; the routes marked admin.access check the
    # pipeline permissions for the request's department in app/services/event_pipeline.py.
    "GET /pipeline/me": "admin.access",
    "PUT /pipeline/teams": "pipeline.teams",
    "GET /pipeline/calendar": "admin.access",
    "PUT /pipeline/calendar/bans": "pipeline.bans",
    "DELETE /pipeline/calendar/bans": "pipeline.bans",
    "POST /pipeline/requests": "admin.access",
    "GET /pipeline/requests": "admin.access",
    "GET /pipeline/requests/{request_id:int}": "admin.access",
    "PUT /pipeline/requests/{request_id:int}/dates": "admin.access",
    "PUT /pipeline/requests/{request_id:int}/details": "admin.access",
    "DELETE /pipeline/requests/{request_id:int}": "admin.access",
    "PUT /pipeline/requests/{request_id:int}/briefs/{team}": "admin.access",
    "POST /pipeline/requests/{request_id:int}/submit": "admin.access",
    "GET /pipeline/notifications": "admin.access",
    "POST /pipeline/notifications/{notification_id:int}/read": "admin.access",
    "POST /pipeline/notifications/read-all": "admin.access",
    "POST /pipeline/sweep": "super_admin",
    "GET /pipeline/inbox": "admin.access",
    "POST /pipeline/requests/{request_id:int}/publish": "admin.access",
    "POST /pipeline/requests/{request_id:int}/return": "pipeline.design",
    "POST /pipeline/requests/{request_id:int}/resubmit": "admin.access",
    "POST /pipeline/requests/{request_id:int}/tasks/{team}/complete": "admin.access",
}


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
