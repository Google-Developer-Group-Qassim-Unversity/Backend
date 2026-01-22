from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB import events as events_queries, forms as form_queries, submissions as submission_queries, logs as log_queries
from ..DB.main import SessionLocal
from app.routers.models import Events_model, ConflictResponse, NotFoundResponse, Form_model, Open_Events_model, Get_Submission_model, createEvent_model, Member_model
from app.config import config
from app.routers.logging import create_log_file, write_log_exception, write_log, write_log_json, write_log_title, write_log_traceback
from app.helpers import admin_guard
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
def create_event(event_data: createEvent_model, credentials = Depends(admin_guard)):
    with SessionLocal() as session:
        log_file = create_log_file("create event")
        try:
            write_log_title(log_file, "Creating New Event and Associated Form")
            # 1. create event
            new_event = events_queries.create_event(session, event_data.event)

            if new_event is None:
                write_log_exception(log_file, f"HTTP 409: An event with the name '{event_data.event.name}' already exists")
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An event with the name '{event_data.event.name}' already exists")
            write_log(log_file, f"Created Event [{new_event.id}]: {new_event.name}")

            # 2. create associated form
            new_form = form_queries.create_form(session, Form_model(
                event_id=new_event.id,
                form_type=event_data.form_type,
            ))

            if new_form is None:
                write_log_exception(log_file, f"HTTP 409: Form with event_id {new_event.id} already exists")
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Form with event_id {new_event.id} already exists")
            write_log(log_file, f"Created Form [{new_form.id}] for Event [{new_event.id}]")

            # 3. create logs for event
            department_log = log_queries.create_log(session, new_event.id, event_data.department_action_id)
            member_log = log_queries.create_log(session, new_event.id, event_data.member_action_id)
            log_queries.create_department_log(session, event_data.department_id, department_log.id)

            write_log(log_file, f"Created logs for event department: [{event_data.department_action_id}] and member: [{event_data.member_action_id}]")




            session.commit()
            session.refresh(new_event)
            return new_event
        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while creating the event")
        finally:
            write_log_json(log_file, event_data.model_dump(mode="json"))

@router.put("/{event_id}", status_code=status.HTTP_200_OK, response_model=Events_model, responses={404: {"model": NotFoundResponse, "description": "Event not found"}, 409: {"model": ConflictResponse, "description": "Event already exists"}})
def update_event(event_id: int, event: Events_model, credentials = Depends(admin_guard)):
    with SessionLocal() as session:
        updated_event = events_queries.update_event(session, event_id, event)
        if updated_event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        if updated_event == -1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An event with the name '{event.name}' already exists")
        session.commit()
    return updated_event

@router.delete("/{event_id:int}", status_code=status.HTTP_200_OK, response_model=Events_model, responses={404: {"model": NotFoundResponse, "description": "Event not found"}})
def delete_event(event_id: int, credentials = Depends(admin_guard)):
    with SessionLocal() as session:
        deleted_event = events_queries.delete_event(session, event_id)
        if not deleted_event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        session.commit()
    return deleted_event

@router.get("/submissions/{event_id:int}", status_code=status.HTTP_200_OK, response_model=list[Get_Submission_model])
def get_submissions_by_event(event_id: int, credentials: HTTPAuthorizationCredentials = Depends(admin_guard)):
    with SessionLocal() as session:
        try:
            submissions_data = submission_queries.get_submissions_by_event_id(session, event_id)
            
            # Transform to Submission_model objects
            submissions = []
            for row in submissions_data:
                member = Member_model(
                    id=row.id,
                    name=row.name,
                    email=row.email,
                    phone_number=row.phone_number,
                    uni_id=row.uni_id,
                    gender=row.gender,
                    uni_level=row.uni_level,
                    uni_college=row.uni_college
                )
                
                submission = Get_Submission_model(
                    member=member,
                    submission_id=row.submission_id,
                    submitted_at=row.submitted_at,
                    form_type=row.form_type,
                    submission_type=row.submission_type,
                    is_accepted=bool(row.is_accepted),
                    google_submission_value=row.google_submission_value,
                    event_id=row.event_id,
                    form_id=row.form_id,
                    google_form_id=row.google_form_id
                )
                submissions.append(submission)
            
            return submissions
        except Exception as e:
            raise