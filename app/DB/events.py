from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from .schema import Events, Forms, t_open_events
from ..routers.models import Events_model

def get_events(session: Session, limit: int = None, offset: int = 0, sort_by: str = "start_datetime", sort_order: str = "DESC"):
    statement = select(Events)
    
    # Apply sorting
    if sort_by == "start_datetime":
        if sort_order == "ASC":
            statement = statement.order_by(Events.start_datetime.asc())
        else:
            statement = statement.order_by(Events.start_datetime.desc())
    
    # Apply pagination
    if limit is not None:
        statement = statement.limit(limit).offset(offset)
    
    events = session.scalars(statement).all()
    return events

def get_open_events(session: Session):
    statement = select(t_open_events)
    results = session.execute(statement).all()
    # Convert Row objects to dictionaries
    return [dict(row._mapping) for row in results]

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
    
    # only update the name if changed
    if existing_event.name != event_data.name:
        name_conflict = session.scalar(
            select(Events).where(Events.name == event_data.name, Events.id != event_id)
        )
        if name_conflict:
            return -1
    
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

def delete_event(session: Session, event_id: int):
    event_to_delete = session.scalar(select(Events).where(Events.id == event_id))
    if not event_to_delete:
        return None
    session.delete(event_to_delete)
    session.flush()
    return event_to_delete