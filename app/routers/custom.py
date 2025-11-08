from typing import List
from fastapi import APIRouter, HTTPException, status
from app.DB import members, logs, actions, departments
from ..DB.main import SessionLocal
from app.routers.models import CustomeBulkMemberData, CustomeMemberReport, customMemberPoints, Member_model, CustomeBulkMemberReport
from app.helpers import get_pydantic_members
from datetime import timedelta, datetime
from app.routers.logging import write_log, summarized_traceback
router = APIRouter()

@router.post("/members/bulk", status_code=201, response_model=CustomeBulkMemberReport)
def custom_bulk_members(data: CustomeBulkMemberData):
    with SessionLocal() as session:
        try:
            # 1. Create new action
            log_file = f"custom_bulk_members {datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            new_action = actions.create_action(session, data.action_name, data.action_points, "custom")

            write_log(log_file, f"[1] Created new action: \x1b[33m{new_action.id}\x1b[0m")

            # 2. Create log for members
            log_members = logs.create_log(session, None, new_action.id)

            write_log(log_file, f"[2] Created log for members: \x1b[33m{log_members.id}\x1b[0m")
            pydantic_members = get_pydantic_members(data.members)

            # 3. Give members points.
            for member, _ in pydantic_members:
                db_member, doesExist = members.create_member_if_not_exists(session, member)

                if doesExist:
                    write_log(log_file, f"[3] Found member: \x1b[33m{db_member.id}\x1b[0m for uni_id \x1b[33m{db_member.uni_id}\x1b[0m")
                else:
                    write_log(log_file, f"[3] Created member: \x1b[33m{db_member.id}\x1b[0m for uni_id \x1b[33m{db_member.uni_id}\x1b[0m")

                member_log = logs.create_member_log(session, db_member.id, log_members.id)
                write_log(log_file, f"[3] Created members_log: \x1b[33m{member_log.id}\x1b[0m")

            # 4. more queries for report (not part of event)
            report = CustomeMemberReport(
                members_count = len(pydantic_members),
                members_points = new_action.points,
                action_name=new_action.action_name
            )

            write_log(log_file, f"\033[32m{'-'*20}\033[0m[Successfully processed bulk members ✅]\033[32m{'-'*20}\033[0m")
            [write_log(log_file, f"{key}: \033[32m{value}\033[0m") for key, value in (report.model_dump().items())]
            write_log(log_file, f"\033[32m{'-'*51}\033[0m")
            return report

        except Exception as e:
            session.rollback()
            write_log(log_file, f"\033[31m{'-'*20}\033[0m[Error processing event ❌]\033[31m{'-'*20}\033[0m\n{e}\n\033[31m{'-'*67}\033[0m")
            write_log(log_file, f"{'-'*20}[Traceback]{'-'*20}")
            summarized_traceback(log_file)
            write_log(log_file, f"{'-'*51}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")

@router.post("/members/", status_code=201, response_model=List[CustomeMemberReport])
def custom_member(body: List[customMemberPoints]):
    try:
        with SessionLocal() as session:
            log_file = f"custom_members_points {datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            report = []
            for member in body:
                # 1. get Member as pydantic model and create them
                db_member_model = Member_model(name=member.name, email=member.email, phone_number=member.phone_number, uni_id=member.uni_id, gender=member.gender)
                db_member, doesExist = members.create_member_if_not_exists(session, db_member_model)

                if doesExist:
                    write_log(log_file, f"[3] Found member: \x1b[33m{db_member.id}\x1b[0m for uni_id \x1b[33m{db_member.uni_id}\x1b[0m")
                else:
                    write_log(log_file, f"[3] Created member: \x1b[33m{db_member.id}\x1b[0m for uni_id \x1b[33m{db_member.uni_id}\x1b[0m")

                # 2. create action and logs

                new_action = actions.create_action(session, member.action_name, member.points, "custom")
                write_log(log_file, f"[1] Created new action: \x1b[33m{new_action.id}\x1b[0m")

                log_member = logs.create_log(session, db_member.id, new_action.id)
                write_log(log_file, f"[2] Created log for members: \x1b[33m{log_member.id}\x1b[0m")

                member_log = logs.create_member_log(session, db_member.id, log_member.id)
                write_log(log_file, f"[3] Created members_log: \x1b[33m{member_log.id}\x1b[0m")

                report.append(
                    CustomeMemberReport(
                        member_name=db_member.name,
                        action_name=new_action.action_name,
                        points=new_action.points
                    )
                )

            session.commit()

            write_log(log_file, f"\033[32m{'-'*20}\033[0m[Successfully processed bulk members ✅]\033[32m{'-'*20}\033[0m")
            for index, report in enumerate(report):
                write_log(log_file, f"{'-'*10} Member[{index+1}] {'-'*10}")
                [write_log(log_file, f"{key}: \033[32m{value}\033[0m") for key, value in (report.model_dump().items())]
            
            write_log(log_file, f"\033[32m{'-'*51}\033[0m")


            return report
        
    except Exception as e:
        write_log(log_file, f"\033[31m{'-'*20}\033[0m[Error processing event ❌]\033[31m{'-'*20}\033[0m\n{e}\n\033[31m{'-'*67}\033[0m")
        write_log(log_file, f"{'-'*20}[Traceback]{'-'*20}")
        summarized_traceback(log_file)
        write_log(log_file, f"{'-'*51}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")
