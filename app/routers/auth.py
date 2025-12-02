from fastapi import APIRouter, Depends
from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer, HTTPAuthorizationCredentials
from app.DB import members
from app.DB.main import SessionLocal
from app.helpers import get_uni_id_from_credentials
from pprint import pprint
router = APIRouter()


# Main auth guard used across the app

clerk_config = ClerkConfig(jwks_url="https://quality-ram-46.clerk.accounts.dev/.well-known/jwks.json") 
clerk_auth_guard = ClerkHTTPBearer(config=clerk_config)


# example endpoint to see jwt contents
@router.get("/auth/users", status_code=200)
def get_users(credentials: HTTPAuthorizationCredentials = Depends(clerk_auth_guard)):
    with SessionLocal() as session:
        member = members.get_member_by_uni_id(session, get_uni_id_from_credentials(credentials))
    print(f"{'-'*10}[Credentials]{'-'*10}")
    pprint(f"{credentials.model_dump()}", indent=4)
    print(f"{'-'*30}")
    return member 






# SUGGESTING FROM ASIIIIIIIIIIIIIIIIIM

# from fastapi import APIRouter, Depends, HTTPException
# from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer, HTTPAuthorizationCredentials
# from sqlalchemy.orm import Session

# from app.DB import members
# from app.DB.main import SessionLocal
# from app.helpers import get_uni_id_from_credentials

# import logging

# router = APIRouter()
# logger = logging.getLogger(__name__)

# clerk_config = ClerkConfig(
#     jwks_url="https://quality-ram-46.clerk.accounts.dev/.well-known/jwks.json"
# )
# clerk_auth_guard = ClerkHTTPBearer(config=clerk_config)



# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()



# @router.get("/auth/users", status_code=200)
# def get_users(
#     credentials: HTTPAuthorizationCredentials = Depends(clerk_auth_guard),
#     db: Session = Depends(get_db)
# ):
#     uni_id = get_uni_id_from_credentials(credentials)

#     member = members.get_member_by_uni_id(db, uni_id)
#     if not member:
#         raise HTTPException(status_code=404, detail="User not found")

#     logger.info("Authenticated user: %s", uni_id)

#     return member
