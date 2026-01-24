from fastapi import APIRouter, status, HTTPException
from app.DB import points as points_queries
from app.DB.main import SessionLocal
from app.routers.models import BaseClassModel
from datetime import datetime
router = APIRouter()

# ============ models ============

class Member_points_model(BaseClassModel):
    member_id: int
    member_name: str
    total_points: int | None = None

class Event_model(BaseClassModel):
    event_name: str
    event_id: int
    start_datetime: datetime
    end_datetime: datetime
    points: int
    action_name: str
    ar_action_name: str | None = None

class Member_event_history_model(BaseClassModel):
    member: Member_points_model
    events: list[Event_model]

class Department_points_model(BaseClassModel):
    department_id: int
    department_name: str
    ar_department_name: str
    department_type: str
    total_points: int

class Response_department_points_model(BaseClassModel):
    administrative: list[Department_points_model]
    practical: list[Department_points_model]

class Department_points_history_model(BaseClassModel):
    department: Department_points_model
    events: list[Event_model]

# ============ routes ============
@router.get("/members/total", status_code=status.HTTP_200_OK, response_model=list[Member_points_model])
def get_all_members_points():
    with SessionLocal() as session: 
        members_points = points_queries.get_all_members_points(session)
    return members_points

@router.get("/members/{member_id}", status_code=status.HTTP_200_OK, response_model=Member_event_history_model)
def get_member_points(member_id: int):
    with SessionLocal() as session:
        member_points = points_queries.get_member_points(session, member_id)
        if member_points is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member points not found or member with id {member_id} does not exist")
        member_points_history = points_queries.get_member_points_history(session, member_id)
    return Member_event_history_model(
        member=member_points,
        events=member_points_history
    )

@router.get("/departments/total", status_code=status.HTTP_200_OK, response_model=Response_department_points_model)
def get_all_departments_points():
    with SessionLocal() as session:
        departments_points = points_queries.get_all_departments_points(session)
    return Response_department_points_model(
        administrative=[department for department in departments_points if department['department_type'] == 'administrative'], 
        practical=[department for department in departments_points if department['department_type'] == 'practical']
    )

@router.get("/departments/{department_id}", status_code=status.HTTP_200_OK, response_model=Department_points_history_model)
def get_department_points(department_id: int):
    with SessionLocal() as session:
        department_points = points_queries.get_department_points(session, department_id)
        if department_points is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department points not found or department with id {department_id} does not exist")
        department_points_history = points_queries.get_department_points_history(session, department_id)
    return Department_points_history_model(
        department=department_points,
        events=department_points_history
    )