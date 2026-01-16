from fastapi import APIRouter, Depends, status, HTTPException
from app.DB.main import SessionLocal
from app.DB import submissions as submission_queries, members as member_queries
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.routers.auth import clerk_auth_guard
from app.helpers import get_uni_id_from_credentials
from app.routers.models import submission_exists_model
router = APIRouter()



@router.post("/{form_id:int}", status_code=status.HTTP_200_OK)
def register_for_event(form_id: int, credentials: HTTPAuthorizationCredentials = Depends(clerk_auth_guard)):
    with SessionLocal() as session:
        try:
            uni_id = get_uni_id_from_credentials(credentials)
            member_id = member_queries.get_member_by_uni_id(session, uni_id).id
            new_submission = submission_queries.create_submission(session, form_id, member_id)
            if not new_submission:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Submission already exists")
            session.commit()
            return new_submission
        except Exception as e:
            session.rollback()
            raise
        
@router.get("/{form_id:int}", status_code=status.HTTP_200_OK, response_model=submission_exists_model)
def check_submission_exists(form_id: int, credentials: HTTPAuthorizationCredentials = Depends(clerk_auth_guard)):
    with SessionLocal() as session:
        try:
            uni_id = get_uni_id_from_credentials(credentials)
            member_id = member_queries.get_member_by_uni_id(session, uni_id).id
            submission = submission_queries.get_submission_by_form_and_member(session, form_id, member_id)
            return {"submitted": submission is not None}
        except Exception as e:
            raise
