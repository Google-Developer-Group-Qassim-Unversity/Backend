from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from .schema import Events, Forms, t_open_events, Logs, DepartmentsLogs, Actions, Departments
from ..routers.models import Events_model

def get_events(session: Session):
    statement = select(Events)
    statement = statement.order_by(Events.start_datetime.desc())
    events = session.scalars(statement).all()
    return events

def get_open_events(session: Session):
    statement = select(t_open_events)
    results = session.execute(statement).all()
    return [dict(row._mapping) for row in results]

def get_actions_by_event_id(session: Session, event_id: int):
    stmt = (
    select(
        Actions.id.label("action_id"),
        Actions.ar_action_name,
        Departments.ar_name.label("department_ar_name"),
        Departments.id.label("department_id"),
    )
    .select_from(Events)
    .join(Logs, Events.id == Logs.event_id)
    .join(Actions, Logs.action_id == Actions.id)
    .outerjoin(DepartmentsLogs, Logs.id == DepartmentsLogs.log_id)
    .outerjoin(Departments, DepartmentsLogs.department_id == Departments.id)
    .where(Events.id == event_id)
    .group_by(
        Actions.id,
        Actions.ar_action_name,
        Departments.ar_name,
        Departments.id,
        Events.id,
    )
    .order_by(Actions.id.asc())
)
    row = session.execute(stmt).all()
    if not row:
        return None
    return [dict(row._mapping) for row in row]

def get_event_by_id(session: Session, event_id: int):
    statement = select(Events).where(Events.id == event_id)
    event = session.scalars(statement).first()
    return event

def create_event(session: Session, event_data: Events_model):
    try:
        new_event = Events(
            name=event_data.name,
            location_type=event_data.location_type,
            location=event_data.location,
            start_datetime=event_data.start_datetime,
            end_datetime=event_data.end_datetime,
            description=event_data.description,
            status=event_data.status,
            is_official=event_data.is_official,
            image_url=event_data.image_url
        )
        session.add(new_event)
        session.flush()
        return new_event
    except IntegrityError as e:
        session.rollback()
        return None

def update_event(session: Session, event_id: int, event_data: Events_model):
    existing_event = session.scalar(select(Events).where(Events.id == event_id))
    if not existing_event:
        return None
    try:
        existing_event.name = event_data.name
        existing_event.location_type = event_data.location_type
        existing_event.location = event_data.location
        existing_event.start_datetime = event_data.start_datetime
        existing_event.end_datetime = event_data.end_datetime
        existing_event.description = event_data.description
        existing_event.status = event_data.status
        existing_event.image_url = event_data.image_url
        session.flush()
        return existing_event
    except IntegrityError as e:
        session.rollback()
        print(f"IntegrityError in update_event: {str(e)}")
        return -1