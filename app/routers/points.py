from fastapi import APIRouter, status
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
    en_action_name: str | None = None
    ar_action_name: str | None = None

class Member_event_history_model(BaseClassModel):
    member_id: int
    member_name: str
    total_points: int
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
        member_points_history = points_queries.get_member_points_history(session, member_id)
    return Member_event_history_model(
        member_id=member_points['member_id'],
        member_name=member_points['member_name'],
        total_points=member_points['total_points'],
        events=member_points_history
    )

@router.get("/departments/total", status_code=status.HTTP_200_OK, response_model=list[Department_points_model])
def get_all_departments_points():
    with SessionLocal() as session:
        departments_points = points_queries.get_all_departments_points(session)
    return departments_points