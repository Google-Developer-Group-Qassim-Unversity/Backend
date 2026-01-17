from fastapi import APIRouter, Depends
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB import members
from app.DB.main import SessionLocal
from app.helpers import get_uni_id_from_credentials, admin_guard
from app.config import config
from json import dumps

router = APIRouter()




# example endpoint to see jwt contents
@router.get("/auth", status_code=200)
def check_if_authenticated(credentials: HTTPAuthorizationCredentials = Depends(config.CLERK_GUARD)):
    
    print(f"{'-'*10}[Credentials]{'-'*10}")
    print(dumps(credentials.model_dump(), ensure_ascii=False, indent=4))
    print(f"{'-'*30}")

    with SessionLocal() as session:
        member = members.get_member_by_uni_id(session, get_uni_id_from_credentials(credentials))
        
    return member 
@router.get("/auth/admin", status_code=200)
def check_if_admin(credentials: HTTPAuthorizationCredentials = Depends(admin_guard)):
    print(f"{'-'*10}[Credentials]{'-'*10}")
    print(dumps(credentials.model_dump(), ensure_ascii=False, indent=4))
    print(f"{'-'*30}")
    return {"message": "You have admin access!"}