"""Creating a full event: the event row, its form, and the logs its points hang off.

``POST /events/`` and the events pipeline's publish both create events, and
must create them the same way, so the steps live here once. The caller commits.
"""

import logging

from sqlalchemy.orm import Session

from app.DB import events as events_queries
from app.DB import forms as form_queries
from app.DB import logs as log_queries
from app.DB.schema import Events, FormType, Logs
from app.routers.models import Form_model, createEvent_model

logger = logging.getLogger(__name__)


def create_full_event(
    session: Session, event_data: createEvent_model, *, responsible_member_id: int, created_by: int
) -> tuple[Events, Logs]:
    """Returns the event and its department log (where department points and discounts go).

    ``responsible_member_id`` is who answers for the event, ``created_by`` who is
    creating it now; the same member on ``POST /events/``, often not in the pipeline.
    """
    # 1. create event
    new_event = events_queries.create_event(session, event_data.event, responsible_member_id, created_by)
    logger.info(
        "Created Event [%s]: %s, responsible member [%s], created by member [%s]",
        new_event.id,
        new_event.name,
        responsible_member_id,
        created_by,
    )

    # 2. create associated form
    new_form = form_queries.create_form(
        session, Form_model(event_id=new_event.id, form_type=FormType(event_data.form_type))
    )
    logger.info(f"Created Form [{new_form.id}] for Event [{new_event.id}]")

    # 3. create logs for event
    department_log = log_queries.create_log(session, new_event.id, event_data.department_action_id)
    # the member-type Logs row is looked up later by (event_id, action_id) via
    # get_attendable_logs, not through this reference, so it is create-only here.
    log_queries.create_log(session, new_event.id, event_data.member_action_id)

    # 4. give department points for each day
    days = (event_data.event.end_datetime - event_data.event.start_datetime).days + 1
    for day in range(days):
        logger.info(f"Giving department {event_data.department_id} points for day [{day + 1}]/[{days}]")
        log_queries.create_department_log(session, event_data.department_id, department_log.id)

    logger.info(
        f"Created logs for event department: [{event_data.department_action_id}] and member: [{event_data.member_action_id}]"
    )
    return new_event, department_log
