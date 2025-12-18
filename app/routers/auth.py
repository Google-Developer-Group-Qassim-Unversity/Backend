from fastapi import APIRouter, Depends
from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer, HTTPAuthorizationCredentials
from app.DB import members
from app.DB.main import SessionLocal
from app.helpers import get_uni_id_from_credentials
from app.config import config
from json import dumps

router = APIRouter()


# Main auth guard used across the app
clerk_config = ClerkConfig(jwks_url=config.CLERK_JWKS_URL)
clerk_auth_guard = ClerkHTTPBearer(config=clerk_config)


# example endpoint to see jwt contents
@router.get("/auth/users", status_code=200)
def get_users(credentials: HTTPAuthorizationCredentials = Depends(clerk_auth_guard)):
    
    print(f"{'-'*10}[Credentials]{'-'*10}")
    print(dumps(credentials.model_dump(), ensure_ascii=False, indent=4))
    print(f"{'-'*30}")

    with SessionLocal() as session:
        member = members.get_member_by_uni_id(session, get_uni_id_from_credentials(credentials))
        
    return member 