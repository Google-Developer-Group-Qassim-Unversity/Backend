from fastapi import APIRouter, Depends
from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer, HTTPAuthorizationCredentials
from app.DB import members
from app.DB.main import SessionLocal
from app.helpers import get_uni_id_from_credentials, is_clerk_dev
from json import dumps
router = APIRouter()


# Main auth guard used across the app
if is_clerk_dev():
    print("Using DEV Clerk ⚠️")
    clerk_config = ClerkConfig(jwks_url="https://quality-ram-46.clerk.accounts.dev/.well-known/jwks.json") 
else:
    clerk_config = ClerkConfig(jwks_url="https://clerk.gdg-q.com/.well-known/jwks.json")
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