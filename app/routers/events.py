from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB import events as events_queries, forms as form_queries, submissions as submission_queries, logs as log_queries, members as member_queries
from ..DB.main import SessionLocal
from app.routers.models import Events_model, ConflictResponse, NotFoundResponse, Form_model, Open_Events_model, Get_Submission_model, createEvent_model, Member_model, BadRequestResponse, InternalServerErrorResponse, UpdateEvent_model, event_actions_model, UpdateEventStatus_model
from app.config import config
from app.routers.logging import create_log_file, write_log_exception, write_log, write_log_json, write_log_title, write_log_traceback
from app.helpers import admin_guard, get_uni_id_from_credentials, validate_attendance_token, credentials_to_member_model
from datetime import datetime
import time
from time import perf_counter
router = APIRouter()

@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Events_model])
def get_all_events():
    log_file = create_log_file("get all events")
    write_log_title(log_file, "Fetching all events")
    start = perf_counter()
    with SessionLocal() as session:
        write_log(log_file, "Querying events from database")
        events = events_queries.get_events(session)
        end = perf_counter()
        write_log(log_file, f"fetched [{len(events)}] events DB took [{(end - start) * 1000 :.2f}]ms to execute")
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

@router.post("/{event_id:int}/attend", status_code=status.HTTP_200_OK, responses={404: {"model": NotFoundResponse, "description": "Event not found"}, 400: {"model": BadRequestResponse, "description": "..."}, 500: {"model": InternalServerErrorResponse, "description": "Internal server error"}})
def mark_attendance(event_id: int, token: str = Query(None, description="Optional attendance token for QR code links"), credentials: HTTPAuthorizationCredentials = Depends(config.CLERK_GUARD)):
    log_file = create_log_file("mark attendance")
    
    # Validate attendance token 
    if token:
        write_log(log_file, f"validating attendance token for event [{event_id}]")
        try:
            token_validation = validate_attendance_token(token, event_id)
            write_log(log_file, f"Token validated successfully for event [{event_id}]")
        except HTTPException as e:
            write_log_exception(log_file, f"Token validation failed reason: '{e.detail}'")
            raise
    else:
        write_log(log_file, f"HTTP 400:No attendance token provided")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No attendance token provided")
    
    with SessionLocal() as session:
        try: 
            member = member_queries.get_member_by_uni_id(session, get_uni_id_from_credentials(credentials))
            write_log_title(log_file, f"Marking attendance for member [{member.name}] with uni_id [{member.uni_id}]")
            # 1. check if event exists
            event = events_queries.get_event_by_id(session, event_id)
            if not event:
                write_log_exception(log_file, f"HTTP 404: Event [{event_id}] not found")
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
            write_log(log_file, f"Attendace for event [{event.name}] for member [{member.name}]")

            event_log = log_queries.get_attendable_logs(session, event_id)
            if not event_log:
                write_log_exception(log_file, f"HTTP 500: Event [{event_id}] has no attendable logs!!")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            write_log(log_file, f"Attendable log found for event [{event_log.id}]")
            

            # 2. check if already marked attendance for today
            member_logs = log_queries.get_member_logs(session, member.id, event_log.id)
            if member_logs is None:
                write_log(log_file, f"No member logs found for member [{member.id}] and event [{event.name}] (has not marked attendance yet)")
            else:
                write_log(log_file, f"Found [{len(member_logs)}] member logs for member [{member.id}] and event [{event.name}]")
                for member_log in member_logs:
                    write_log(log_file, f"Checking member log id: [{member_log.id}], log_id: [{member_log.log_id}], Date: [{member_log.date}]")
                    # check if its for today
                    if member_log.date.date() == datetime.now().date(): # the .date() removes the time part of the datetime
                        write_log(log_file, f"Member [{member.id}] has already marked attendance for today the [{member_log.date.day}]th")
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You have already marked attendance for today")
                    else:
                        write_log(log_file, f"Member [{member.id}] was attended for the [{member_log.date.day}]th")

            # 3. get form registration type
            form = form_queries.get_form_by_event_id(session, event_id)
            if not form:
                write_log_exception(log_file, f"HTTP 500: Form for event [{event_id}] not found!!")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            write_log(log_file, f"Form found: [{form.id}] of type [{form.form_type}]")
            form_type = form.form_type

            if form_type == 'none':
                write_log(log_file, f"Form type is none, creating member log for member [{member.id}] and log [{event_log.id}]")
                log_queries.create_member_log(session, member.id, event_log.id)
                write_log(log_file, f"Member marked attendance for event [{event.name}]")


            if form_type == 'google' or form_type == 'registration':
                write_log(log_file, f"Form type is google or registration, checking submissions for member [{member.id}] and form [{form.id}]")
                submissions = submission_queries.get_submission_by_form_and_member(session, form.id, member.id)
                if not submissions:
                    write_log_exception(log_file, f"HTTP 400: Member [{member.id}] has not submitted the form [{form.id}]")
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You have not submitted the form for this event")
                if submissions.is_accepted == 0:
                    write_log_exception(log_file, f"HTTP 400: Member [{member.id}] has not been accepted to the event [{event.name}]")
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You have not been accepted to the event")

                write_log(log_file, f"Member [{member.id}] has submitted the form and been accepted to the event [{event.name}], marking attendance...")
                log_queries.create_member_log(session, member.id, event_log.id)
                write_log(log_file, f"Member marked attendance for today the [{datetime.now().date().day}]th for event [{event.name}]")

            session.commit()
            return

        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

@router.get("/open", status_code=status.HTTP_200_OK, response_model=list[Open_Events_model])
def get_registrable_events():
    """returns events + their associated form """
    log_file = create_log_file("get open events")
    with SessionLocal() as session:
        start = perf_counter()
        write_log(log_file, "Querying open events from database")
        open_events = events_queries.get_open_events(session)
        end = perf_counter()
        write_log(log_file, f"fetched [{len(open_events)}] open events DB took [{(end - start) * 1000 :.2f}]ms to execute")
    return open_events

@router.get("/{event_id:int}/details", status_code=status.HTTP_200_OK, response_model=UpdateEvent_model)
def get_event_details(event_id: int, credentials: HTTPAuthorizationCredentials = Depends(admin_guard)):
    """return an event + its associated actions, this is needed by the frontend to populate the update event form with the current event data and associated actions"""
    with SessionLocal() as session:
        event = events_queries.get_event_by_id(session, event_id)
        actions = events_queries.get_actions_by_event_id(session, event_id)
        if not event or not actions:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return {
        "event": event,
        "actions": actions
    }

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

            # 4. give department points for each day
            days = (event_data.event.end_datetime - event_data.event.start_datetime).days + 1
            for day in range(days):
                write_log(log_file, f"Giving department points for day [{day + 1}]/[{days}]")
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

@router.put("/{event_id:int}", status_code=status.HTTP_200_OK, response_model=Events_model, responses={404: {"model": NotFoundResponse, "description": "Event not found"}, 409: {"model": ConflictResponse, "description": "Event already exists"}, 500: {"model": InternalServerErrorResponse, "description": "Internal server error"}})
def update_event(event_id: int, event_data: UpdateEvent_model, credentials = Depends(admin_guard)):
    log_file = create_log_file("update event")
    with SessionLocal() as session:
        try:
            write_log_title(log_file, f"Updating Event [{event_id}]")
            
            # 1. Validate event exists and update event fields
            updated_event = events_queries.update_event(session, event_id, event_data.event)
            if updated_event is None:
                write_log_exception(log_file, f"HTTP 404: Event [{event_id}] not found")
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
            if updated_event == -1:
                write_log_exception(log_file, f"HTTP 409: An event with the name '{event_data.event.name}' already exists")
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An event with the name '{event_data.event.name}' already exists")
            write_log(log_file, f"Updated Event [{event_id}]: {updated_event.name}")

            # 2. Get all logs for this event
            logs = log_queries.get_logs_by_event_id(session, event_id)
            if not logs or len(logs) < 2:
                write_log_exception(log_file, f"HTTP 500: Event [{event_id}] does not have expected logs")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Event logs not found")
            write_log(log_file, f"Found [{len(logs)}] logs for event [{event_id}]")

            # 3. Identify department log and member log
            # Department log has DepartmentsLogs entries, member log doesn't
            department_log = None
            member_log = None
            for log in logs:
                current_dept_id = log_queries.get_department_id_from_log(session, log.id)
                if current_dept_id is not None:
                    department_log = log
                else:
                    member_log = log
            
            if not department_log or not member_log:
                write_log_exception(log_file, f"HTTP 500: Could not identify department and member logs for event [{event_id}]")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not identify logs")

            # 4. Actions list: first = department action, second = member action
            department_action = event_data.actions[0]
            member_action = event_data.actions[1]
            write_log(log_file, f"Department action: [{department_action.action_id}], Member action: [{member_action.action_id}]")

            # 5. Update department log action_id
            log_queries.update_log_action_id(session, department_log.id, department_action.action_id)
            write_log(log_file, f"Updated department log [{department_log.id}] action_id to [{department_action.action_id}]")

            # 6. Update member log action_id
            log_queries.update_log_action_id(session, member_log.id, member_action.action_id)
            write_log(log_file, f"Updated member log [{member_log.id}] action_id to [{member_action.action_id}]")

            # 7. Handle department_id and/or days change
            current_dept_id = log_queries.get_department_id_from_log(session, department_log.id)
            new_dept_id = department_action.department_id
            current_dept_logs_count = log_queries.get_department_logs_count(session, department_log.id)
            new_days = (updated_event.end_datetime - updated_event.start_datetime).days + 1
            
            write_log(log_file, f"Current: dept_id=[{current_dept_id}], days=[{current_dept_logs_count}]. New: dept_id=[{new_dept_id}], days=[{new_days}]")
            
            if new_dept_id is not None and current_dept_id != new_dept_id:
                # Department changed - delete all and recreate
                write_log(log_file, f"Department changed from [{current_dept_id}] to [{new_dept_id}]")
                
                deleted_count = log_queries.delete_department_logs_by_log_id(session, department_log.id)
                write_log(log_file, f"Deleted [{deleted_count}] old department logs for log [{department_log.id}]")
                
                for day in range(new_days):
                    log_queries.create_department_log(session, new_dept_id, department_log.id)
                write_log(log_file, f"Created [{new_days}] department logs for new department [{new_dept_id}]")
                
            elif current_dept_logs_count != new_days:
                # Same department but days changed
                dept_id_to_use = new_dept_id if new_dept_id is not None else current_dept_id
                
                if new_days > current_dept_logs_count:
                    # Days increased - add more department logs
                    days_to_add = new_days - current_dept_logs_count
                    write_log(log_file, f"Days increased from [{current_dept_logs_count}] to [{new_days}], adding [{days_to_add}] department logs")
                    for _ in range(days_to_add):
                        log_queries.create_department_log(session, dept_id_to_use, department_log.id)
                else:
                    # Days decreased - remove some department logs
                    days_to_remove = current_dept_logs_count - new_days
                    write_log(log_file, f"Days decreased from [{current_dept_logs_count}] to [{new_days}], removing [{days_to_remove}] department logs")
                    log_queries.delete_n_department_logs(session, department_log.id, days_to_remove)
            
            session.commit()
            session.refresh(updated_event)
            write_log(log_file, f"Event [{event_id}] updated successfully")
            return updated_event
            
        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while updating the event")
        finally:
            write_log_json(log_file, event_data.model_dump(mode="json"))
            
@router.put("/{event_id:int}/status", status_code=status.HTTP_200_OK, response_model=Events_model, responses={404: {"model": NotFoundResponse, "description": "Event not found"}})
def update_event_status(event_id: int, status_data: UpdateEventStatus_model, credentials = Depends(admin_guard)):
    with SessionLocal() as session:
        event = events_queries.get_event_by_id(session, event_id)
        if not event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        event.status = status_data.status
        session.commit()
        session.refresh(event)
    return event

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