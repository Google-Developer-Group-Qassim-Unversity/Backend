from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from .schema import Events, Logs
from ..routers.models import Events_model

def get_events(session: Session):
    statement = select(Events)
    events = session.scalars(statement).all()
    return events

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
            status=event_data.status
        )
        session.add(new_event)
        session.flush()
        return new_event
    except IntegrityError as e:
        session.rollback()
        return e

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