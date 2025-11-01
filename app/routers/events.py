from fastapi import APIRouter, HTTPException, status
from app.DB import events as events_queries
from ..DB.main import SessionLocal
from app.routers.models import Events_model, ConflictResponse, NotFoundResponse
router = APIRouter()

@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Events_model])
def get_all_events():
    with SessionLocal() as session:
        events = events_queries.get_events(session)
    return events

@router.get("/{event_id}", status_code=status.HTTP_200_OK, response_model=Events_model, responses={404: {"model": NotFoundResponse, "description": "Event not found"}})
def get_event_by_id(event_id: int):
    with SessionLocal() as session:
        event = events_queries.get_event_by_id(session, event_id)
        if not event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        session.flush()
    return event


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=Events_model, responses={409: {"model": ConflictResponse, "description": "Event already exists"}})
def create_event(event: Events_model):
    with SessionLocal() as session:
        new_event = events_queries.create_event(session, event)
        if not new_event:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An event with the name '{event.name}' already exists")
        session.commit()
    return new_event

@router.put("/{event_id}", status_code=status.HTTP_200_OK, response_model=Events_model, responses={404: {"model": NotFoundResponse, "description": "Event not found"}})
def update_event(event_id: int, event: Events_model):
    with SessionLocal() as session:
        updated_event = events_queries.update_event(session, event_id, event)
        if updated_event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        session.commit()
    return updated_event