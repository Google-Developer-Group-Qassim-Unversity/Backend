from sqlalchemy import select 
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from db import *
from models import Member, Action, Categorized_action
from typing import List
router = APIRouter()

def to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

@router.get("/", status_code=200)
def hanlde_root():
    return HTMLResponse("<h1>Score Admin API is ready ✅</h1>")

@router.get("/members", status_code=200)
def handle_members():
    with SessionLocal() as session:
        members = session.scalars(select(Members)).all()

        return members[0].email

@router.put("/members", response_model=Member, status_code=201)
def update_member(member: Member):


    with SessionLocal() as session:
        db_member = session.get(Members, member.id)
        
        if not db_member:
            print(f"member id {member.id}")
            raise HTTPException(status_code=404, detail="Member not found in db.")
        
        db_member.name = member.name
        db_member.email = member.email
        db_member.phone_number = member.phone_number
        db_member.uni_id = member.uni_id

        session.commit()
        session.refresh(db_member)

        return db_member
    

@router.post("/members", status_code=201, response_model=Member)
def add_member(member: Member):
    with SessionLocal() as session:
        new_member = Members(
            name=member.name, 
            email=member.email,
            phone_number=member.phone_number,
            uni_id=member.uni_id
        )

        session.add(new_member)
        session.commit()
        session.refresh(new_member)

    return new_member


@router.get("/actions", status_code=200, response_model=Categorized_action)
def get_actions():
    with SessionLocal() as session:
        statement = select(Actions)
        result = session.scalars(statement).all()
        composite_actions = []
        department_actions = []
        member_actions = []        
        for action in result:
            if action.action_type == "composite":
                composite_actions.append(action)
            elif action.action_type == "member":
                member_actions.append(action)
            elif action.action_type == "department":
                department_actions.append(action)

    return Categorized_action(
        composite_actions = composite_actions,
        member_actions = member_actions,
        department_actions = department_actions
    )

@router.post("/actions", status_code=201, response_model=Action)
def add_action(action: Action):
    with SessionLocal() as session:
        new_action = Actions(
            english_action_name=action.action_name,
            arabic_action_name=action.action_arabic_name, 
            action_type=action.action_type,
            action_description=action.action_description, 
            points=action.points
        )

        session.add(new_action)
        session.commit()
        session.refresh(new_action)

    return new_action


# @router.put("/events")
# def update_event(event: )