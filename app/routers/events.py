from fastapi import APIRouter, HTTPException
from app.DB import events as events_queries
from ..DB.main import SessionLocal
from app.routers.models import Events_model
from sqlalchemy.exc import IntegrityError
router = APIRouter()

@router.get("/", status_code=200, response_model=list[Events_model])
def get_all_events():
    with SessionLocal() as session:
        events = events_queries.get_events(session)
        return events

@router.get("/{event_id}", status_code=200, response_model=Events_model)
def get_event_by_id(event_id: int):
    with SessionLocal() as session:
        event = events_queries.get_event_by_id(session, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    session.flush()
    return event

@router.post("/", status_code=201, response_model=Events_model)
def create_event(event: Events_model):
    try:
        with SessionLocal() as session:
            new_event = events_queries.create_event(session, event)
            session.commit()
            return new_event
    except IntegrityError as e:
        print(f"IntegrityError: {str(e)[:50]}...")
        session.rollback()
        raise HTTPException(status_code=409, detail=f"An event with the name '{event.name}' already exists")
    
@router.put("/{event_id}", status_code=200, response_model=Events_model)
def update_event(event_id: int, event: Events_model):
    with SessionLocal() as session:
        updated_event = events_queries.update_event(session, event_id, event)
        if updated_event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        session.commit()
        return updated_event