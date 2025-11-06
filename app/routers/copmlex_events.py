import traceback
from fastapi import APIRouter, HTTPException, status
from app.DB import events, members, logs, actions
from ..DB.main import SessionLocal
from app.routers.models import ConflictResponse, NotFoundResponse, CompositeEventData, CompositeEventReport
from app.helpers import get_pydantic_members
from datetime import timedelta, datetime
from os import makedirs
router = APIRouter()

makedirs("event_logs", exist_ok=True)
def write_log(file: str, message: str):

    try:
        with open(f"event_logs/{file}", 'x') as f:
            pass
    except FileExistsError:
        pass

    print(message)
    with open(f"event_logs/{file}", "a") as f:
        f.write(message + "\n")

@router.post("/composite", status_code=status.HTTP_201_CREATED, response_model=CompositeEventReport, responses={409: {"model": ConflictResponse, "description": "Conflict: Event already exists"}})
def create_composite_event(body: CompositeEventData):
    log_file = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}]-{body.event_info.name.replace(' ', '_')}.log"
    try:
        with SessionLocal() as session:
            write_log(log_file, f"\033[34m{'-'*20}\033[0m[Processing Composite Event {body.event_info.name}]\033[34m{'-'*20}\033[0m")
            # 1. create event
            new_event = events.create_event(session, body.event_info)
            if not new_event:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An event with the name '{body.event_info.event_title}' already exists")

            write_log(log_file, f"[1] Created event: \x1b[33m{new_event.id}\x1b[0m")

            # 2. extract members
            members_data = get_pydantic_members(body.members_attendance)

            # 3. give members points: create a log for members and create member_logs and absences
            log_members = logs.create_log(session, new_event.id, body.member_action_id)
            write_log(log_file, f"[3] Created log for members: \x1b[33m{log_members.id}\x1b[0m")
            for member, days_present in members_data:
                db_member = members.create_member_if_not_exists(session, member)
                write_log(log_file, f"[3] Created member: \x1b[33m{db_member.id}\x1b[0m for uni_id \x1b[33m{db_member.uni_id}\x1b[0m")
                member_log = logs.create_member_log(session, db_member.id, log_members.id)
                write_log(log_file, f"[3] Created members_log: \x1b[33m{member_log.id}\x1b[0m")

                for i, day in enumerate(days_present): # [present, absent, absent]
                    if day == "Absent":
                        absence = logs.create_absence(session, member_log.id, body.event_info.start_date + timedelta(days=i))
                        write_log(log_file, f"[3] Created absence: \x1b[33m{absence.id}\x1b[0m")

            # 4. (give departments points) create a log for department and create a department_log and add bonuses
            log_department = logs.create_log(session, new_event.id, body.department_action_id)
            write_log(log_file, f"[4] Created department log: \x1b[33m{log_department.id}\x1b[0m")

            department_log = logs.create_department_log(session, body.department_id, log_department.id)
            write_log(log_file, f"[4] Created department log: \x1b[33m{department_log.id}\x1b[0m")

            if body.bonus > 0:
                bonus = logs.create_modification(session, log_department.id, 'bonus', body.bonus)
                write_log(log_file, f"[4.1] Created bonus for department log: \x1b[33m{bonus.id}\x1b[0m")
            if body.discount > 0:
                discount = logs.create_modification(session, log_department.id, 'discount', body.discount)
                write_log(log_file, f"[4.2] Created discount for department log: \x1b[33m{discount.id}\x1b[0m")

            # 4. queries for the report (not part of the event creation)
            days = timedelta(days=body.event_info.end_date.day - body.event_info.start_date.day + 1).days
            members_points = actions.get_action_by_id(session, body.member_action_id).points * days
            department_points = actions.get_action_by_id(session, body.department_action_id).points * days

            session.commit()
            write_log(f"\033[32m{'-'*20}\033[0m[Successfully processed event ✅]\033[32m{'-'*20}\033[0m")
            return CompositeEventReport(
                event=new_event,
                members_count=len(members_data),
                members_points=members_points,
                department_points=department_points,
            )
    except Exception as e:
        session.rollback()
        write_log(log_file, f"\033[31m{'-'*20}\033[0m[Error processing event ❌]\033[31m{'-'*20}\033[0m\n{e}\n\033[31m{'-'*67}\033[0m")
        write_log(log_file, f"{'-'*20}[Traceback]{'-'*20}\n{traceback.format_exc()}{'-'*51}")

        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")