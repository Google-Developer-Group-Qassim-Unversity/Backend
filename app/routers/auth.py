from fastapi import APIRouter, Depends
from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from app.DB import members
from app.DB.main import SessionLocal
router = APIRouter()
import os

clerk_config = ClerkConfig(jwks_url="https://quality-ram-46.clerk.accounts.dev/.well-known/jwks.json") 
clerk_auth_guard = ClerkHTTPBearer(config=clerk_config)

@router.get("/auth/users", status_code=200)
def get_users(credentials: HTTPAuthorizationCredentials = Depends(clerk_auth_guard)):
    with SessionLocal() as session:
        member = members.get_member_by_uni_id(session, credentials.model_dump().get("uiId"))
    print(credentials.model_dump())
    return member