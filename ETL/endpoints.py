from sqlalchemy import select 
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from db import *
from helpers import csv_to_pydantic_member
from models import Member, Action, Categorized_action, FormData, Department
from typing import List
import datetime
from pprint import pprint
router = APIRouter()

def to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

@router.get("/", status_code=200)
def hanlde_root():
    return HTMLResponse("<h1>Score Admin API is ready ✅</h1>")

@router.post("/events", status_code=200)
def handle_events(form_data: FormData):

    # This needs to be refactored into smaller functions.
    # that take the same session. so that multiple queryies
    # can be done in one transaction.

    members_data = csv_to_pydantic_member(str(form_data.members_link))
    with SessionLocal() as session: # @ibrahim make this a transaction
        try:

            print(f"Creating event: \x1b[33m{form_data.event_info.event_title}\x1b[0m")
            new_event = Events(
                name=form_data.event_info.event_title,
            )

            session.add(new_event)
            session.flush()
            print(f"Event created with id: \x1b[32m{new_event.id}\x1b[0m")


            print(f"Creating log for event: \x1b[33m{form_data.event_info.event_title}\x1b[0m")
            new_log = Logs(
                action_id=form_data.action_id,
                event_id=new_event.id,
                start_date=form_data.event_info.start_date,
                end_date=form_data.event_info.end_date
            )

            session.add(new_log)
            session.flush()
            print(f"Log created with id: \x1b[32m{new_log.id}\x1b[0m")

            print(f"Creating modification for log id: \x1b[33m{new_log.id}\x1b[0m")
            if form_data.bonus > 0:
                new_bonus = Modifications(
                    log_id=new_log.id,
                    type = "bonus",
                    value= form_data.bonus
                )
                session.add(new_bonus)
                print(f"Modification created with id: \x1b[32m{new_bonus.id}\x1b[0m")
            if form_data.discount > 0:
                new_discount = Modifications(
                    log_id=new_log.id,
                    type = "discount",
                    value= form_data.discount
                )
                session.add(new_discount)
                session.flush()
                print(f"Modification created with id: \x1b[32m{new_discount.id}\x1b[0m")

            event_date = form_data.event_info.start_date

            print(f"Processing \x1b[33m{len(members_data)}\x1b[0m members from csv")
            for member, days_present in members_data:
                member: Member
                days_present: list

                print(f"searching for member: \x1b[33m{member.name}\x1b[0m")
                db_member = session.execute(
                    select(Members).where(Members.uni_id == member.uni_id)
                ).scalar_one_or_none()

                
                if not db_member:
                    db_member = Members(
                        name=member.name,
                        email=member.email,
                        phone_number=member.phone_number,
                        uni_id=member.uni_id,
                        gender=member.gender
                    )
                print(f"Adding member: \x1b[33m{member.name}\x1b[0m")
                session.add(db_member)
                session.flush()
                print(f"Member id: \x1b[32m{db_member.id}\x1b[0m")

                print(f"Creating member log for member: \x1b[33m{member.name}\x1b[0m")
                new_members_logs = MembersLogs(
                    member_id=db_member.id,
                    log_id=new_log.id,
                )

                session.add(new_members_logs)
                session.flush()

                for i, day in enumerate(days_present): # [present, absent, absent]
                    if day == "absent":
                        new_absence = Absence(
                            member_log_id=new_members_logs.id,
                            date=event_date + datetime.timedelta(days=i)
                        )
                        session.add(new_absence)
                
            # if there is an organizer they need their own log
            organizers = len(form_data.Organizers) if form_data.Organizers != None else 0
            if organizers > 0:
                print(f"Processing \x1b[33m{form_data.Organizers}\x1b[0m organizers")
                for member in form_data.Organizers:
                    db_member = session.execute(
                        select(Members).where(Members.uni_id == member.uni_id)
                    ).scalar_one_or_none()

                    if not db_member:
                        db_member = Members(
                            name=member.name,
                            email=member.email,
                            phone_number=member.phone_number,
                            uni_id=member.uni_id,
                            gender=member.gender
                        )

                    session.add(db_member)
                    session.flush()

                    print(f"Creating Organizer log for event: \x1b[33m{form_data.event_info.event_title}\x1b[0m")
                    org_new_log = Logs(
                        action_id=member.participation_type,
                        event_id=new_event.id,
                        start_date=form_data.event_info.start_date,
                        end_date=form_data.event_info.end_date
                    )

                    session.add(org_new_log)
                    session.flush()
                    print(f"Organizer log created with id: \x1b[32m{org_new_log.id}\x1b[0m")

                    new_members_logs = MembersLogs(
                        member_id=db_member.id,
                        log_id=org_new_log.id,
                    )

                    session.add(new_members_logs)
                print(f"processed organizer: \x1b[32m{len(form_data.Organizers) if form_data.Organizers != None else 0}\x1b[0m")
            else:
                print("No organizers to process")    


            print(f"Linking department id: \x1b[33m{form_data.department_id}\x1b[0m to log id: \x1b[33m{new_log.id}\x1b[0m")
            new_departments_logs = DepartmentsLogs(
                id=form_data.department_id,
                department_id=form_data.department_id,
                log_id=new_log.id
            )
            print(f"Department log created with id: \x1b[32m{new_departments_logs.id}\x1b[0m")

            session.add(new_departments_logs)
            session.commit()
            print(f"Event processing completed successfully ✅")
            return JSONResponse(status_code=200, content={"message": "Event processed successfully"})
        except Exception as e:
            session.rollback()
            print("Error processing event ❌")
            print(e)
            raise HTTPException(status_code=500, detail="Internal Server Error") 



@router.get("/members", status_code=200, response_model=List[Member])
def handle_members():
    with SessionLocal() as session:
        members = session.scalars(select(Members)).all()

    return members

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

@router.get("/departments", status_code=200, response_model=List[Department])
def handle_department():
    with SessionLocal() as session:
        result = session.scalars(select(Departments)).all()
        departments = []
        for department in result:
            departments.append(department)

    return departments

@router.get("/actions", status_code=200, response_model=Categorized_action)
def get_actions():
    with SessionLocal() as session:
        statement = select(Actions)
        result = session.scalars(statement).all()
        composite_Actions = []
        department_Actions = []
        member_Actions = []
        for action in result:
            if action.action_type == "composite":
                composite_Actions.append(action)
            elif action.action_type == "member":
                member_Actions.append(action)
            elif action.action_type == "department":
                department_Actions.append(action)

    return Categorized_action(
    composite_actions = composite_Actions,
    member_actions = member_Actions,
    department_actions = department_Actions
    )

@router.get("/actions/contributers", response_model=List[Action], status_code=200)
def get_action_contributors():
    with SessionLocal() as session:
        statement = select(Actions).where(
            Actions.action_name.in_(["volunteer", "Presented a course"])
        )
        result = session.scalars(statement).all()
    return result

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
