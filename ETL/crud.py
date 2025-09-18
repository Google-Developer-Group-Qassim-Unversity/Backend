from sqlalchemy import select 
from fastapi import FastAPI, HTTPException, Response
from db import *
from models import Member

app = FastAPI()

@app.put("/members")
def handle_update_member(member: Member):


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

        return Response(status_code=204)
    
@app.get("/actions")
def get_actions():
    with SessionLocal() as session:
        stmt = select(Actions)
        result = session.scalars(stmt).all()
        actions = []
        
        for action in result:
            actions.append(action)

        return {
            "actions": actions
        }
