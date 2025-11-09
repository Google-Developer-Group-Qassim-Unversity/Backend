from fastapi import APIRouter, HTTPException, status
from app.DB import logs
from ..DB.main import SessionLocal
from app.routers.models import Department_model, NotFoundResponse, Member_model
from typing import List
router = APIRouter()


@router.get("/events", status_code=status.HTTP_200_OK)
def get_editable_complex_events():
    with SessionLocal() as session:
        try:
            expanded_members_logs = logs.get_expanded_members_logs(session)
            return expanded_members_logs

        except Exception as e:
            print(e)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error fetching editable complex events")

@router.delete("/events/members/{log_id: int}", status_code=status.HTTP_200_OK, response_model=List[Member_model], responses={404: {"model": NotFoundResponse, "description": "Member or log not found"}})
def delete_members_from_event(log_id: int, member_IDs: List[int]):
    with SessionLocal() as session:
        try:
            deleted = []
            for member_id in member_IDs:
                deleted_member = logs.delete_member_log(session, log_id, member_id)
                if not deleted_member:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with ID {member_id} was not found with event log {log_id}")
                deleted.append(deleted_member)
            session.commit()

            return deleted

        except Exception as e:
            print(e)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error deleting members from event")