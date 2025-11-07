from os import path
import traceback
import sys
from fastapi import APIRouter, HTTPException, status
from app.DB import events, members, logs, actions, departments
from ..DB.main import SessionLocal
from app.routers.models import ConflictResponse, NotFoundResponse, CompositeEventData, BaseEventReport, CompositeEventReport, DepartmentEventData
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

def summarized_traceback(log_file: str):
    tb = sys.exc_info()[2]
    for frame in traceback.extract_tb(tb):
        write_log(log_file, f"...{path.sep.join(frame.filename.split(path.sep)[-3:])}, line {frame.lineno}:{frame.colno}")

@router.post("/composite", status_code=status.HTTP_201_CREATED, response_model=CompositeEventReport, responses={409: {"model": ConflictResponse, "description": "Conflict: Event already exists"}})
def create_composite_event(body: CompositeEventData):
    log_file = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}]-{body.event_info.name.replace(' ', '_')}.log"
    try:
        with SessionLocal() as session:
            write_log(log_file, f"\033[34m{'-'*20}\033[0m[Processing Composite Event {body.event_info.name}]\033[34m{'-'*20}\033[0m")
            # 1. create event
            new_event = events.create_event(session, body.event_info)
            if not new_event:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An event with the name '{body.event_info.name}' already exists")

            write_log(log_file, f"[1] Created event: \x1b[33m{new_event.id}\x1b[0m")

            # 2. extract members
            members_data = get_pydantic_members(body.members_attendance)

            # 3. give members points: create a log for members and create member_logs and absences
            log_members = logs.create_log(session, new_event.id, body.member_action_id)

            # 3.1 add bonuses/discounts to members log
            if body.member_bonus > 0:
                bonus = logs.create_modification(session, log_members.id, 'bonus', body.member_bonus)
                write_log(log_file, f"[3] Created bonus for members log: \x1b[33m{bonus.id}\x1b[0m")
            if body.member_discount > 0:
                discount = logs.create_modification(session, log_members.id, 'discount', body.member_discount)
                write_log(log_file, f"[3] Created discount for members log: \x1b[33m{discount.id}\x1b[0m")

            write_log(log_file, f"[3] Created log for members: \x1b[33m{log_members.id}\x1b[0m")

            # 3.2 create a log for each member and create absences
            for member, days_present in members_data:
                db_member, doesExist = members.create_member_if_not_exists(session, member)
                if doesExist:
                    write_log(log_file, f"[3] Found member: \x1b[33m{db_member.id}\x1b[0m for uni_id \x1b[33m{db_member.uni_id}\x1b[0m")
                else:
                    write_log(log_file, f"[3] Created member: \x1b[33m{db_member.id}\x1b[0m for uni_id \x1b[33m{db_member.uni_id}\x1b[0m")

                member_log = logs.create_member_log(session, db_member.id, log_members.id)
                write_log(log_file, f"[3] Created members_log: \x1b[33m{member_log.id}\x1b[0m")

                for i, day in enumerate(days_present): # [present, absent, absent]
                    if day == "Absent":
                        absence = logs.create_absence(session, member_log.id, body.event_info.start_datetime + timedelta(days=i))
                        write_log(log_file, f"[3] Created absence: \x1b[33m{absence.id}\x1b[0m")

            # 4. give departments points create a log for department and create a department_log and add bonuses
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
            days = timedelta(days=body.event_info.end_datetime.day - body.event_info.start_datetime.day + 1).days
            members_points = (actions.get_action_by_id(session, body.member_action_id).points + body.member_bonus) * days
            department_points = (actions.get_action_by_id(session, body.department_action_id).points + body.department_bonus) * days
            department = departments.get_department_by_id(session, body.department_id).name


            # 5 commit and report
            session.commit()

            report = CompositeEventReport(
                event=new_event,
                days=days,
                members_count=len(members_data),
                members_points=members_points,
                department=department,
                department_points=department_points,
            )
            write_log(log_file, f"\033[32m{'-'*20}\033[0m[Successfully processed event ✅]\033[32m{'-'*20}\033[0m")
            [write_log(log_file, f"{key}: \033[32m{value}\033[0m") for key, value in (report.model_dump()['event'].items())]

            report_dict = report.model_dump()
            del report_dict['event']
            [write_log(log_file, f"{key}: \033[32m{value}\033[0m") for key, value in (report_dict.items())]
            write_log(log_file, f"\033[32m{'-'*51}\033[0m")
            return report
        
    except HTTPException as http_exc:
        write_log(log_file, f"\033[31m{'-'*20}\033[0m[Error processing event ❌]\033[31m{'-'*20}\033[0m\n{http_exc}\n\033[31m{'-'*67}\033[0m")
        raise http_exc
    except Exception as e:
        session.rollback()
        write_log(log_file, f"\033[31m{'-'*20}\033[0m[Error processing event ❌]\033[31m{'-'*20}\033[0m\n{e}\n\033[31m{'-'*67}\033[0m")
        write_log(log_file, f"{'-'*20}[Traceback]{'-'*20}")
        summarized_traceback(log_file)
        write_log(log_file, f"{'-'*51}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")
    


@router.post("/department", status_code=status.HTTP_201_CREATED, response_model=CompositeEventReport, responses={409: {"model": ConflictResponse, "description": "Conflict: Event already exists"}})
def create_department_event(body: DepartmentEventData ):
    try:
        log_file = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}]-{body.event_info.name.replace(' ', '_')}.log"
        with SessionLocal() as session:
            write_log(log_file, f"\033[34m{'-'*20}\033[0m[Processing Composite Event {body.event_info.name}]\033[34m{'-'*20}\033[0m")
            # 1. create event
            new_event = events.create_event(session, body.event_info)
            if not new_event:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An event with the name '{body.event_info.name}' already exists")
            write_log(log_file, f"[1] Created event: \x1b[33m{new_event.id}\x1b[0m")

            # 2. give departments points create a log for department and create a department_log and add bonuses
            log_department = logs.create_log(session, new_event.id, body.department_action_id)
            write_log(log_file, f"[2] Created department log: \x1b[33m{log_department.id}\x1b[0m")

            department_log = logs.create_department_log(session, body.department_id, log_department.id)
            write_log(log_file, f"[2] Created department log: \x1b[33m{department_log.id}\x1b[0m")

            if body.bonus > 0:
                bonus = logs.create_modification(session, log_department.id, 'bonus', body.bonus)
                write_log(log_file, f"[2] Created bonus for department log: \x1b[33m{bonus.id}\x1b[0m")

            # 3 queries for the report (not part of the event creation)
            days = timedelta(days=body.event_info.end_datetime.day - body.event_info.start_datetime.day + 1).days
            department = departments.get_department_by_id(session, body.department_id).name
            department_points = (actions.get_action_by_id(session, body.department_action_id).points + body.department_bonus) * days

            session.commit()
            
            result = BaseEventReport(
                event=new_event,
                days=days,
                department=department,
                department_points=department_points
            )

            write_log(log_file, f"\033[32m{'-'*20}\033[0m[Successfully processed event ✅]\033[32m{'-'*20}\033[0m")
            [write_log(log_file, f"{key}: \033[32m{value}\033[0m") for key, value in (result.model_dump()['event'].items())]

            result_dict = result.model_dump()
            del result_dict['event']
            [write_log(log_file, f"{key}: \033[32m{value}\033[0m") for key, value in (result_dict.items())]
            write_log(log_file, f"\033[32m{'-'*51}\033[0m")

            return result

    except HTTPException as http_exc:
        write_log(log_file, f"\033[31m{'-'*20}\033[0m[Error processing event ❌]\033[31m{'-'*20}\033[0m\n{http_exc}\n\033[31m{'-'*67}\033[0m")
        raise http_exc
    except Exception as e:
        session.rollback()
        write_log(log_file, f"\033[31m{'-'*20}\033[0m[Error processing event ❌]\033[31m{'-'*20}\033[0m\n{e}\n\033[31m{'-'*67}\033[0m")
        write_log(log_file, f"{'-'*20}[Traceback]{'-'*20}")
        summarized_traceback(log_file)
        write_log(log_file, f"{'-'*51}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")