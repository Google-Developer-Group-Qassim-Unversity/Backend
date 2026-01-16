from fastapi import APIRouter, Depends, HTTPException, status, Query
from app.DB import events as events_queries
from app.DB import forms as form_queries
from ..DB.main import SessionLocal
from app.routers.models import Events_model, ConflictResponse, NotFoundResponse, Form_model, Open_Events_model
from app.config import config
router = APIRouter()

@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Events_model])
def get_all_events(
    limit: int = Query(default=config.DEFAULT_PAGE_SIZE, le=config.MAX_PAGE_SIZE, ge=1, description="Number of events to return"),
    offset: int = Query(default=0, ge=0, description="Number of events to skip"),
    sort_by: str = Query(default=config.DEFAULT_EVENTS_SORT_BY, description="Field to sort by"),
    sort_order: str = Query(default=config.DEFAULT_SORT_ORDER, description="Sort order (ASC or DESC)")
):
    with SessionLocal() as session:
        events = events_queries.get_events(session, limit=limit, offset=offset, sort_by=sort_by, sort_order=sort_order.upper())
    return events

@router.get("/{event_id:int}", status_code=status.HTTP_200_OK, response_model=Events_model, responses={404: {"model": NotFoundResponse, "description": "Event not found"}})
def get_event_by_id(event_id: int):
    with SessionLocal() as session:
        event = events_queries.get_event_by_id(session, event_id)
        if not event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        session.flush()
    return event

        

@router.get("/{event_id:int}/form", status_code=status.HTTP_200_OK, response_model=Form_model, responses={404: {"model": NotFoundResponse, "description": "Form not found"}})
def get_event_form(event_id: int):
    with SessionLocal() as session:
        form = form_queries.get_form_by_event_id(session, event_id)
        if not form:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    return form

@router.get("/open", status_code=status.HTTP_200_OK, response_model=list[Open_Events_model])
def get_registrable_events():
    with SessionLocal() as session:
        open_events = events_queries.get_open_events(session)
    return open_events

@router.post("/", status_code=status.HTTP_201_CREATED, response_model=Events_model, responses={409: {"model": ConflictResponse, "description": "Event already exists"}})
def create_event(event: Events_model):
    with SessionLocal() as session:
        new_event = events_queries.create_event(session, event)
        if not new_event:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An event with the name '{event.name}' already exists")
        session.commit()
        session.refresh(new_event)
    return new_event

@router.put("/{event_id}", status_code=status.HTTP_200_OK, response_model=Events_model, responses={404: {"model": NotFoundResponse, "description": "Event not found"}, 409: {"model": ConflictResponse, "description": "Event already exists"}})
def update_event(event_id: int, event: Events_model):
    with SessionLocal() as session:
        updated_event = events_queries.update_event(session, event_id, event)
        if updated_event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        if updated_event == -1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An event with the name '{event.name}' already exists")
        session.commit()
    return updated_event

@router.delete("/{event_id:int}", status_code=status.HTTP_200_OK, response_model=Events_model, responses={404: {"model": NotFoundResponse, "description": "Event not found"}})
def delete_event(event_id: int):
    with SessionLocal() as session:
        deleted_event = events_queries.delete_event(session, event_id)
        if not deleted_event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        session.commit()
    return deleted_event