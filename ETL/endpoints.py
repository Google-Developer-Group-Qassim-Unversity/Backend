from sqlalchemy import select, exists
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse, HTMLResponse
from db import *
from helpers import csv_to_pydantic_member
from models import CompositeFormData, Member, Action, Categorized_action, CompositeFormData, Department, DepartmentFormData, OrganizerData, MemberFormData, CustomMembersFormData, CustomDepartmentsFormData
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
def handle_events(form_data: CompositeFormData):

    # This needs to be refactored into smaller functions.
    # that take the same session. so that multiple queryies
    # can be done in one transaction.

    # return JSONResponse(status_code=200, content={"message": "Event processed successfully"})

    members_data = csv_to_pydantic_member(str(form_data.members_link))
    with SessionLocal() as session:
        try:

            is_event_exist = session.scalar(select(exists().where(Events.name == form_data.event_info.event_title)))

            if is_event_exist:
                raise HTTPException(status_code=400, detail={
                    "error": "Event already exist with that name",
                    "detail": form_data.event_info.event_title
                })

            print(f"Creating event: \x1b[33m{form_data.event_info.event_title}\x1b[0m")
            new_event = Events(
                name=form_data.event_info.event_title,
            )

            session.add(new_event)
            session.flush()
            print(f"Event created with id: \x1b[32m{new_event.id}\x1b[0m")


            print(f"Creating log for department for event: \x1b[33m{form_data.event_info.event_title}\x1b[0m")
            dept_new_log = Logs(
                action_id=form_data.department_action_id,
                event_id=new_event.id,
                start_date=form_data.event_info.start_date,
                end_date=form_data.event_info.end_date
            )

            session.add(dept_new_log)
            session.flush()
            print(f"Log for department created with id: \x1b[32m{dept_new_log.id}\x1b[0m")

            print(f"Linking department id: \x1b[33m{form_data.department_id}\x1b[0m to log id: \x1b[33m{dept_new_log.id}\x1b[0m")
            new_departments_log = DepartmentsLogs(
                department_id=form_data.department_id,
                log_id=dept_new_log.id,
                attendants_number=len(members_data)
            )

            session.add(new_departments_log)
            session.flush()
            print(f"Department log created with id: \x1b[32m{new_departments_log.id}\x1b[0m")


            print(f"Creating modification for log id: \x1b[33m{dept_new_log.id}\x1b[0m")
            if form_data.bonus > 0:
                new_bonus = Modifications(
                    log_id=dept_new_log.id,
                    type="bonus", 
                    value=form_data.bonus
                )

                session.add(new_bonus)
                session.flush()
                print(f"Modification with type 'bonus' and value {new_bonus.value} was created for this log id: \x1b[32m{dept_new_log.id}\x1b[0m")

 
            if form_data.discount > 0:
                new_discount = Modifications(
                    log_id=dept_new_log.id, 
                    type="discount", 
                    value=form_data.discount
                )

                session.add(new_discount)
                session.flush()
                print(f"Modification with type 'discount' and value {new_discount.value} was created for this log id: \x1b[32m{dept_new_log.id}\x1b[0m")


            print(f"Creating log for members for event: \x1b[33m{form_data.event_info.event_title}, Action_id: {form_data.member_action_id}\x1b[0m")
            member_new_log = Logs(
                action_id=form_data.member_action_id,
                event_id=new_event.id,
                start_date=form_data.event_info.start_date,
                end_date=form_data.event_info.end_date
            )

            session.add(member_new_log)
            session.flush()
            print(f"Log for members created with id: \x1b[32m{member_new_log.id}\x1b[0m")

            event_start_date = form_data.event_info.start_date

            print(f"Processing \x1b[33m{len(members_data)}\x1b[0m members from csv")
            for member, days_present in members_data:
                member: Member
                days_present: list

                print(f"searching for member: \x1b[33m{member.name}\x1b[0m")
                db_member = session.execute(
                    select(Members).where(Members.uni_id == member.uni_id)
                ).scalar_one_or_none()

                
                if not db_member:
                    print(f"Member \x1b[33m{member.uni_id}\x1b[0m not found in db, creating new member")
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
                    log_id=member_new_log.id,
                )

                session.add(new_members_logs)
                session.flush()

                for i, day in enumerate(days_present): # [present, absent, absent]
                    if day == "absent":
                        new_absence = Absence(
                            member_log_id=new_members_logs.id,
                            date=event_start_date + datetime.timedelta(days=i)
                        )
                        session.add(new_absence)
                
            # Flow for organizers
            organizers = len(form_data.organizers) if form_data.organizers != None else 0
            if organizers > 0:
                print(f"Processing \x1b[33m{form_data.organizers}\x1b[0m organizers")
                for member in form_data.organizers:
                    member: OrganizerData

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

                    print(f"Creating log for organizer for event: \x1b[33m{form_data.event_info.event_title}\x1b[0m")
                    org_new_log = Logs(
                        action_id=member.participation_action_id,
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

                    for i, day in enumerate(member.attendance):
                        if day == "absent":
                            new_absence = Absence(
                                member_log_id = db_member.id, 
                                date = event_start_date + datetime.timedelta(days=i)
                            )

                            session.add(new_absence)
                            session.flush()

                print(f"processed organizer: \x1b[32m{len(form_data.organizers) if form_data.organizers != None else 0}\x1b[0m")
            else:
                print("No organizers to process")    

            session.commit()
            print(f"Event processing completed successfully ✅")
            return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Event processed successfully"})
        except Exception as e:
            session.rollback()
            print("Error processing event ❌")
            print(e)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error") 


@router.post("/events/departments", status_code=status.HTTP_200_OK)
def handle_departments(form_data: DepartmentFormData):
    with SessionLocal() as session:
        try:
            # check if event already exist in DB
            is_event_exist = session.scalar(select(exists().where(Events.name == form_data.event_info.event_title)))
            if is_event_exist:                
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
                    "error": "Event already exist with that name",
                    "detail": form_data.event_info.event_title
                })
            
            # insert event into DB
            print(f"Creating event: \x1b[33m{form_data.event_info.event_title}\x1b[0m")
            new_event = Events(
                name=form_data.event_info.event_title
            )

            session.add(new_event)
            session.flush()
            print(f"Event created with id: \x1b[32m{new_event.id}\x1b[0m")

            # create and insert a log into DB
            print(f"Creating log for department for event: \x1b[33m{form_data.event_info.event_title}\x1b[0m")
            new_log = Logs(
                action_id=form_data.action_id,
                start_date=form_data.event_info.start_date,
                end_date=form_data.event_info.end_date,
                event_id=new_event.id
            )

            session.add(new_log)
            session.flush()
            print(f"Log for department created with id: \x1b[32m{new_log.id}\x1b[0m")

            # check then adding dicounts and bonuses
            if form_data.discount > 0:
                new_modification = Modifications(
                    log_id=new_log.id,
                    type="discount",
                    value=form_data.discount
                )                 
                session.add(new_modification)


            if form_data.bonus > 0:
                new_modification = Modifications(
                    log_id=new_log.id, 
                    type="bonus", 
                    value=form_data.bonus
                )


            # link the log to dept log 
            new_dept_log = DepartmentsLogs(
                department_id=form_data.department_id,
                log_id=new_log.id
            )


            session.add(new_dept_log)
            session.flush()
            print(f"Department log created with id: \x1b[32m{new_dept_log.id}\x1b[0m")


            # origanizers flow
            organizers = len(form_data.organizers) if form_data.organizers != None else 0
            if organizers > 0:
                print(f"Processing \x1b[33m{form_data.organizers}\x1b[0m organizers")
                members = form_data.organizers
                for member in members:
                    member: OrganizerData
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

                    print(f"Creating log for organizer for event: \x1b[33m{form_data.event_info.event_title}\x1b[0m")
                    org_new_log = Logs(
                        action_id=member.participation_action_id,
                        event_id=new_event.id,
                        start_date=form_data.event_info.start_date,
                        end_date=form_data.event_info.end_date
                    )

                    session.add(org_new_log)
                    session.flush()
                    print(f"Organizer log created with id: \x1b[32m{org_new_log.id}\x1b[0m")

                    new_members_log = MembersLogs(
                        member_id=db_member.id,
                        log_id=org_new_log.id 
                    )

                    session.add(new_members_log)
                    session.flush()


                    # processing attendance for organizers
                    attendance = member.attendance
                    for i, day in enumerate(attendance):
                        if day == "absent":
                            new_absence=Absence(
                                member_log_id=new_members_log.id,
                                date=form_data.event_info.start_date + datetime.timedelta(days=i)
                            )

                            session.add(new_absence)

                print(f"processed organizer: \x1b[32m{len(form_data.organizers) if form_data.organizers != None else 0}\x1b[0m")
            else:
                print(f"processed organizer: \x1b[32m{len(form_data.organizers) if form_data.organizers != None else 0}\x1b[0m")

            session.commit()
            print(f"Event processing completed successfully ✅")
            return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Event processed successfully"})
        except Exception as e:
            session.rollback()
            print("Error processing event ❌")
            print(e)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")


@router.post("/events/members", status_code=status.HTTP_200_OK)
def handle_members(form_data: MemberFormData):
    with SessionLocal() as session:
        try:
            # checking if event exist then adding it if not
            is_event_exist = session.scalar(select(exists().where(Events.name == form_data.event_info.event_title)))
            if is_event_exist:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
                    "error": "Event already exist with that name",
                    "detail": form_data.event_info.event_title
                })
            
            new_event = Events(
                name=form_data.event_info.event_title
            )

            session.add(new_event)
            session.flush()
            print(f"Event Created. \x1b[32m{form_data.event_info.event_title}\x1b[0m")

            # creating a log for the event
            new_log = Logs(
                    action_id=form_data.action_id,
                    start_date=form_data.event_info.start_date, 
                    end_date=form_data.event_info.end_date, 
                    event_id=new_event.id
                )

            session.add(new_log)
            session.flush()
            print(f"Log Created. \x1b[32m{new_log.id}\x1b[0m")


            if form_data.bonus > 0:
                new_modification = Modifications(
                    log_id=new_log.id, 
                    type="bonus", 
                    valu=form_data.bonus
                )

                session.add(new_modification)
            
            if form_data.discount > 0:
                new_modification = Modifications(
                    log_id=new_log.id, 
                    type="discount", 
                    value=form_data.discount
                )

                session.add(new_modification)

            # adding members and all that kind of crap 
            members = form_data.members
            for member in members:
                member: Member

                new_member = session.execute(select(Members).where(Member.uni_id == member.uni_id)).scalar_one_or_none()

                if not new_member:
                    new_member = Members(
                        name=member.name, 
                        email=member.email, 
                        phone_number=member.phone_number, 
                        uni_id=member.uni_id, 
                        gender=member.gender
                    )

                    session.add(new_member)
                    session.flush()
                    print(f"Member was added succussfuly \x1b[32m{new_member.name}\x1b[0m")

                # link linking members to perviously created log
                new_member_log = MembersLogs(
                    member_id=new_member.id,
                    log_id=new_log.id
                )

                session.add(new_member_log)
                print(f"Member Log Created. \x1b[32m{new_member_log.id}\x1b[0m")

            session.commit()
            print(f"Event processing completed successfully ✅")
            return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Event processed successfully"})
        except Exception as e:
            session.rollback()
            print("Error processing event ❌")
            print(e)
            # {'error': 'Event already exist with that name', 'detail': 'asdf'}
            try:
                if e['error'] == "Event already exist with that name":
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e)
            except Exception as ex:
                pass
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")


@router.get("/members", status_code=status.HTTP_200_OK, response_model=List[Member])
def handle_get_members():
    with SessionLocal() as session:
        members = session.scalars(select(Members)).all()

    return members


@router.put("/members", response_model=Member, status_code=status.HTTP_201_CREATED)
def handle_update_member(member: Member):


    with SessionLocal() as session:
        try:
            db_member = session.get(Members, member.id)
            
            if not db_member:
                print(f"member id {member.id}")
                raise HTTPException(status_code=404, detail="Member not found in db.")
            
            db_member.name = member.name
            db_member.email = member.email
            db_member.phone_number = member.phone_number
            db_member.uni_id = member.uni_id
            db_member.gender = member.gender

            session.commit()
            session.refresh(db_member)
            return db_member
        except Exception as e:
            session.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"Internal server error."})


@router.post("/members", status_code=status.HTTP_201_CREATED, response_model=Member)
def handle_create_member(member: Member):
    with SessionLocal() as session:
        try:
            new_member = Members(
                name=member.name, 
                email=member.email,
                phone_number=member.phone_number,
                uni_id=member.uni_id,
                gender=member.gender
            )

            session.add(new_member)
            session.commit()
            session.refresh(new_member)

            return new_member
        except Exception as e:
            session.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"Internal server error"})


@router.get("/departments", status_code=status.HTTP_200_OK, response_model=List[Department])
def handle_department():
    with SessionLocal() as session:
        result = session.scalars(select(Departments)).all()
        departments = []
        for department in result:
            departments.append(department)

    return departments


@router.get("/actions", status_code=status.HTTP_200_OK, response_model=Categorized_action)
def get_actions():
    with SessionLocal() as session:
        statement = select(Actions)
        result = session.scalars(statement).all()
        department_Actions = []
        member_Actions = []
        custom_action = []
        for action in result:
            if action.action_type == "member":
                member_Actions.append(to_dict(action))
            elif action.action_type == "department":
                department_Actions.append(to_dict(action))
            elif action.action_name == "Bonus":
                custom_action.append(to_dict(action))


        ids_group1 = [51, 52, 53, 54]
        ids_group2 = [76, 77, 78, 79]

        # Query
        rows1 = session.execute(select(Actions).where(Actions.id.in_(ids_group1))).scalars().all()
        id_to_row1 = {row.id: row for row in rows1}
        ordered_rows1 = [id_to_row1[i] for i in ids_group1]

        rows2 = session.execute(select(Actions).where(Actions.id.in_(ids_group2))).scalars().all()
        id_to_row2 = {row.id: row for row in rows2}
        ordered_rows2 = [id_to_row2[i] for i in ids_group2]

        # Serialize and pair
        paired = [
            (to_dict(a1), to_dict(a2))
            for a1, a2 in zip(ordered_rows1, ordered_rows2)
        ]

    return Categorized_action(
        composite_actions=paired,
        department_actions=department_Actions,
        member_actions=member_Actions,
        custom_actions=custom_action,
    )


@router.get("/actions/contributers", response_model=List[Action], status_code=status.HTTP_200_OK)
def get_action_contributors():
    with SessionLocal() as session:
        statement = select(Actions).where(
            Actions.action_name.in_(["volunteer", "Presented a course"])
        )
        result = session.scalars(statement).all()
    return result


@router.post("/actions", status_code=status.HTTP_201_CREATED, response_model=Action)
def add_action(action: Action):

    with SessionLocal() as session:
        try:
            new_action = Actions(
                english_action_name=action.action_name,
                arabic_action_name=action.arabic_action_name, 
                action_type=action.action_type,
                action_description=action.action_description, 
                points=action.points
            )

            session.add(new_action)
            session.commit()
            session.refresh(new_action)

            return new_action
        
        except Exception as e:
            session.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"Internal server error"})
        

@router.get("/events", status_code=status.HTTP_200_OK, response_model=List[str])
def handler_get_events():
    with SessionLocal() as session:
        try:
            db_events = session.scalars(select(Events.name)).all()
            
            events = []
            for event in db_events:
                events.append(event)

            return events
        except Exception:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

@router.post("/events/custom/members", status_code=status.HTTP_201_CREATED)
def handler_custom_members(form_data: CustomMembersFormData,):
    with SessionLocal() as session:
        try:
            # check if event exist in DB
            is_event_exist = session.scalar(select(exists().where(Events.name == form_data.event_info.event_title)))
            

            if is_event_exist:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
                    "error": "Event already exist with that name",
                    "detail": form_data.event_info.event_title
                })
            
            print(f"Creating event: \x1b[33m{form_data.event_info.event_title}\x1b[0m")
            new_event = Events(
                name=form_data.event_info.event_title
            )

            session.add(new_event)
            session.flush()
            print(f"Event Created. \x1b[32m{new_event.name}\x1b[0m")

            # add a log to the event 
            print(f"Creating log for members for event: \x1b[33m{form_data.event_info.event_title}, Action_id: {form_data.action_id}\x1b[0m")
            new_log = Logs(
                action_id=form_data.action_id, 
                event_id=new_event.id, 
                start_date=form_data.event_info.start_date, 
                end_date=form_data.event_info.end_date
            )

            session.add(new_log)
            session.flush()
            print(f"Log Created. \x1b[32m{new_log.id}\x1b[0m")

            print(f"Creating modification for log id: \x1b[33m{new_log.id}\x1b[0m")
            new_modification = Modifications(
                log_id=new_log.id, 
                type="bonus", 
                value=form_data.bonus
            )

            session.add(new_modification)
            session.flush()
            print(f"Bonus Row Created With Value. \x1b[32m{new_modification.value}\x1b[0m")


            members = form_data.members
            for member in members:
                member: Member
                print(f"Searching for member: \x1b[33m{member.name}\x1b[0m")
                db_member = session.execute(select(Members).where(Members.uni_id == member.uni_id)).scalar_one_or_none()
                

                if not db_member:
                    print(f"Member \x1b[33m{member.uni_id}\x1b[0m not found in db, creating new member")
                    db_member = Member(
                        name=member.name, 
                        email=member.email, 
                        phone_number=member.phone_number, 
                        uni_id=member.uni_id, 
                        gender=member.gender
                    )

                    session.add(db_member)
                    session.flush()
                    print(f"Member Created. \x1b[32m{db_member.id}\x1b[0m")
                else:
                    print(f"Member Found. \x1b[32m{db_member.id}\x1b[0m")

                print(f"Linking member id: \x1b[33m{db_member.id}\x1b[0m to log id: \x1b[33m{new_log.id}\x1b[0m")
                # linking member to the created log
                new_member_log = MembersLogs(
                    member_id=db_member.id, 
                    log_id=new_log.id
                )

                session.add(new_member_log)
                session.flush()
                print(f"Member Log Created. \x1b[32m{new_member_log.id}\x1b[0m")

            session.commit()
            print(f"Event processing completed successfully ✅")
            return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Event processed successfully"})
        except Exception as e:
            session.rollback()
            print("Error processing event ❌")
            print(e)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error") 


@router.post("/events/custom/departments", status_code=status.HTTP_201_CREATED)
def handle_custom_departments(form_data: CustomDepartmentsFormData):
    with SessionLocal() as session:
        try:
            # check if event exist in DB
            is_event_exist = session.scalar(select(exists().where(Events.name == form_data.event_info.event_title)))
            
            if is_event_exist:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
                    "error": "Event already exist with that name",
                    "detail": form_data.event_info.event_title
                })
            
            print(f"Creating event: \x1b[33m{form_data.event_info.event_title}\x1b[0m")
            new_event = Events(
                name=form_data.event_info.event_title
            )

            session.add(new_event)
            session.flush()
            print(f"Event Created. \x1b[32m{new_event.name}\x1b[0m")

            # add a log to the event 
            print(f"Creating log for members for event: \x1b[33m{form_data.event_info.event_title}, Action_id: {form_data.action_id}\x1b[0m")
            new_log = Logs(
                action_id=form_data.action_id, 
                event_id=new_event.id, 
                start_date=form_data.event_info.start_date, 
                end_date=form_data.event_info.end_date
            )

            session.add(new_log)
            session.flush()
            print(f"Log Created. \x1b[32m{new_log.id}\x1b[0m")

            print(f"Creating modification for log id: \x1b[33m{new_log.id}\x1b[0m")
            new_modification = Modifications(
                log_id=new_log.id, 
                type="bonus", 
                value=form_data.bonus
            )

            session.add(new_modification)
            session.flush()
            print(f"Bonus Row Created With Value. \x1b[32m{new_modification.value}\x1b[0m")

            print(f"Linking department id: \x1b[33m{form_data.department_id}\x1b[0m to log id: \x1b[33m{new_log.id}\x1b[0m")
            # link the log to dept log 
            new_dept_log = DepartmentsLogs(
                department_id=form_data.department_id,
                log_id=new_log.id
            )


            session.add(new_dept_log)
            session.flush()
            print(f"Department log created with id: \x1b[32m{new_dept_log.id}\x1b[0m")

            session.commit()
            print(f"Event processing completed successfully ✅")
            return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Event processed successfully"})
        except Exception as e:
            session.rollback()
            print("Error processing event ❌")
            print(e)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error") 