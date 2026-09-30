"""Which department(s) a request is about, for ``Require(perm, resolver)``.

A resolver reads the path parameters and returns the departments the thing
belongs to. ``Require`` calls it only after the caller has the permission
somewhere, so a regular user is refused without learning whether the thing
exists; for everyone else a missing thing is a 404, not a 403.
"""

from sqlalchemy.orm import Session

from app.DB import events as event_queries
from app.DB import forms as form_queries
from app.exceptions import NotFound


def event_departments(session: Session, path: dict) -> frozenset[int]:
    event_id = int(path["event_id"])
    event_queries.get_event_by_id(session, event_id)  # raises EventNotFound
    return frozenset(event_queries.get_event_department_ids(session, event_id))


def form_departments(session: Session, path: dict) -> frozenset[int]:
    form_id = int(path["form_id"])
    form = form_queries.get_form_by_id(session, form_id)
    if form is None:
        raise NotFound("Form", form_id)
    return frozenset(event_queries.get_event_department_ids(session, form.event_id))


def path_departments(session: Session, path: dict) -> frozenset[int]:
    return frozenset({int(path["department_id"])})


# How ``Require`` names each resolver in its auth label ("events.edit for event").
for _resolver, _label in ((event_departments, "event"), (form_departments, "form"), (path_departments, "department")):
    setattr(_resolver, "label", _label)
