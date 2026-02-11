from typing import List
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPAuthorizationCredentials
from app.DB import events as events_queries, departments as departments_queries, actions as actions_queries, logs as log_queries, members as members_queries
from app.DB.main import SessionLocal
from app.routers.models import Events_model, BaseClassModel
from app.helpers import get_pydantic_members, admin_guard
from datetime import timedelta, datetime
from app.routers.logging import write_log, write_log_traceback, create_log_file, write_log_exception, write_log_json, write_log_title
router = APIRouter()

class DepartmentPointDetails(BaseClassModel):
    departments_id: list[int]
    points: int
    action_id: int | None = None
    action_name: str | None = None


class CustomDepartmentPointsRequest(BaseClassModel):
    event_id: int | None = None
    start_datetime: datetime
    end_datetime: datetime
    event_name: str
    point_deatils: List[DepartmentPointDetails]

@router.post("/departments", status_code=status.HTTP_201_CREATED)
def give_department_custom_points(body: CustomDepartmentPointsRequest, credentials: HTTPAuthorizationCredentials = Depends(admin_guard)):
    log_file = create_log_file("custom_department_points")
    with SessionLocal() as session:
        try:
            write_log_title(log_file, "Custom Department Points")
            # [1] validate events
            if body.event_id:
                write_log(log_file, f"Validating event with id {body.event_id}")
                event = events_queries.get_event_by_id(session, body.event_id)
                if not event:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Event with id {body.event_id} not found")
            else:
                # an existing event is not provided, create a new one with default values
                write_log(log_file, f"No event id provided, creating a new event with name {body.event_name}")
                new_event_model = Events_model(
                    name=body.event_name,
                    description=None,
                    location="none",
                    location_type="none",
                    start_datetime=body.start_datetime,
                    end_datetime=body.end_datetime,
                    status="closed",
                    image_url=None,
                    is_official=0
                )

                event = events_queries.create_event(session, new_event_model)
                if not event:
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create event")
            
            # Proccess each point detail and give points to departments per action/points
            details_len = len(body.point_deatils)
            for i, point_detail in enumerate(body.point_deatils):
                # [2] validate action
                write_log(log_file, f"Processing point detail [{i+1}/{details_len}]")
                if point_detail.action_id:
                    write_log(log_file, f"Validating action with id {point_detail.action_id}")
                    action = actions_queries.get_action_by_id(session, point_detail.action_id)
                    if not action:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Action with id {point_detail.action_id} not found")
                else:
                    write_log(log_file, f"No action id provided, infering with points ")
                    if point_detail.points > 0:
                        action = actions_queries.get_bonus_action(session)
                        write_log(log_file, f"Points are {point_detail.points}, using Bonus action {action.action_name} with id {action.id}")
                    else:
                        action = actions_queries.get_discount_action(session)
                        write_log(log_file, f"Points are {point_detail.points}, using Discount action {action.action_name} with id {action.id}")
                
                # [3] using the action and event to give points
                new_log = log_queries.create_log(session, event.id, action.id)
                write_log(log_file, f"Created log with id {new_log.id} for event {event.name} and action {action.action_name}")

                # [4] creating bunus/discount modificatoin
                mod_type = "bonus" if point_detail.points > 0 else "discount"
                mod_value = abs(point_detail.points)
                log_queries.create_modification(session, new_log.id, mod_type, mod_value)
                write_log(log_file, f"Created modification for log id {new_log.id} with type {mod_type} and value {mod_value}")

                # [5] give points to departments
                for department_id in point_detail.departments_id:
                    write_log(log_file, f"Giving points to department with id {department_id}")
                    department_log = log_queries.create_department_log(session, department_id, new_log.id)

            session.commit()
            write_log(log_file, "Successfully given custom points to departments")
            return {"message": "Successfully given custom points to departments"}
            
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while giving custom points to the department")
        