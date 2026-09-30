"""Which department(s) a request is about, for ``Require(perm, resolver)``.

Each resolver takes path parameters by name, like a handler would, and
returns the departments the thing belongs to. A missing thing is a 404 here,
before any permission is checked, so "not found" never reads as "forbidden".
"""

from app.DB import events as event_queries
from app.DB import forms as form_queries
from app.dependencies import DB
from app.exceptions import NotFound


def event_departments(event_id: int, session: DB) -> frozenset[int]:
    event_queries.get_event_by_id(session, event_id)  # raises EventNotFound
    return frozenset(event_queries.get_event_department_ids(session, event_id))


def form_departments(form_id: int, session: DB) -> frozenset[int]:
    form = form_queries.get_form_by_id(session, form_id)
    if form is None:
        raise NotFound("Form", form_id)
    return frozenset(event_queries.get_event_department_ids(session, form.event_id))


def path_departments(department_id: int) -> frozenset[int]:
    return frozenset({department_id})


# How ``Require`` names each resolver in its auth label ("events.edit for event").
for _resolver, _label in ((event_departments, "event"), (form_departments, "form"), (path_departments, "department")):
    setattr(_resolver, "label", _label)
