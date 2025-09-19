from sqlalchemy import select 
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse
from db import *
from models import Member

router = APIRouter()

@router.put("/members")
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

        return JSONResponse(
            status_code=204,
            content={
                "id": db_member.id,
                "name": db_member.name,
                "email": db_member.email, 
                "phone number": db_member.phone_numer,
                "uni_id": db_member.uni_id
            }
            )


@router.get("/actions")
def get_actions():
    with SessionLocal() as session:
        stmt = select(Actions)
        result = session.scalars(stmt).all()
        actions = []
        
        for action in result:
            actions.append(action)

    return {"actions": actions}
    

@router.post("/members")
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

    return JSONResponse(
        status_code=201, 
        content={
            "id": new_member.id,
            "name": new_member.name,
            "email": new_member.email,
            "phone_number": new_member.phone_number,
            "uni_id": new_member.uni_id
        }
    )


