from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB import departments as departments_queries
from ..DB.main import SessionLocal
from app.routers.models import Department_model, NotFoundResponse
from app.config import config
from app.helpers import admin_guard
from app.helpers import get_uni_id_from_credentials
import json
router = APIRouter()

@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Department_model])
def get_all_departments():
	with SessionLocal() as session:
		departments = departments_queries.get_departments(session)
	return departments

@router.get("/{department_id:int}", status_code=status.HTTP_200_OK, response_model=Department_model, responses={404: {"model": NotFoundResponse, "description": "Department not found"}})
def get_department_by_id(department_id: int):
	with SessionLocal() as session:
		department = departments_queries.get_department_by_id(session, department_id)
		if not department:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {department_id} not found")
	return department

@router.get("/votes", status_code=status.HTTP_200_OK)
def get_departments_vote_count():
	with open("votes_counts.json", "r") as f:
		departments = json.load(f)
		return departments

@router.post("/votes/{department_id:int}", status_code=status.HTTP_201_CREATED)
def vote_for_department(department_id: int, credentials: HTTPAuthorizationCredentials = Depends(config.CLERK_GUARD)):

	# check if member has already voted

	member_uni_id = get_uni_id_from_credentials(credentials)
	with open("votes_members.json", "r") as f:
		voted_members = json.load(f)
	if member_uni_id in voted_members:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Member has already voted")

	# record that member has voted
	if department_id < 1 or department_id > 9:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {department_id} not found")
	with open("votes_counts.json", "r") as f:
		departments_votes = json.load(f)

	departments_votes[str(department_id)]['votes'] = departments_votes[str(department_id)]['votes'] + 1

	with open("votes_counts.json", "w") as f:
		json.dump(departments_votes, f, indent=4)

	# add member to voted members
	with open("votes_members.json", "r") as f:
		votes_members = json.load(f)

	votes_members.append(member_uni_id)

	with open("votes_members.json", "w") as f:
		json.dump(votes_members, f, indent=4)
	return departments_votes