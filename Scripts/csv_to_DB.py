from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from schema import *
import csv
import datetime
import time
import urllib
import io
import json
import sys
from dotenv import load_dotenv
from os import getenv

load_dotenv()
engine = create_engine(getenv("DATABASE_URL"))
Session = sessionmaker(bind=engine)
session = Session()


def main():

    links = load_args()
    data = load_department_csv(links['Main_sheet'])

    department = "Tech and Business"
    department_action = "On-site course"
    event_name = "مبادئ كرة القدم"
    event_date = datetime.date(2025, 9, 12)

    pass

def load_args():
    json_text = sys.stdin.read()
    
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        print("Invalid JSON:", e)

    return data


def load_department_csv(sheet_url):
    """
    loading the 'main_sheet' returns the following:
    [
        {
            'Bonus': '0',
            'Discount': '0',
            'Event Title': 'ورشة تصميم كيبورد',    
            'department name': 'Tech and Business',
            'end date': '11 Sep 2025',
            'list of attendants': 'TRUE',
            'list of contributors': 'TRUE',        
            'start date': '11 Sep 2025',
            'type of the event': 'On-site course'
        }
    ]
    """
    print(f"Downloading ${sheet_url[0:10]}...")
    response = urllib.request.urlopen(sheet_url)
    csv_data = response.read().decode("utf-8")
    csv_file = io.StringIO(csv_data)
    data = [row for row in csv.DictReader(csv_file)]

    return data

def test_connection():
    start = time.time()
    print(f"Querying DB...")
    result = session.query(Action).all()
    print("Done ✅")
    end = time.time()
    for action in result:
        print(action.id, action.name)
    print(f"Query Took {end - start}ms ⌛")

def add_department_event(department_name, department_action_name, event_date, event_name):
    print("Adding department event to DB...")
    try:
        with session.begin():
            # Department log
            dept_action = session.query(Action).filter_by(name=department_action_name).one()
            dept_log = Log(
                action_id=dept_action.id,
                start_date=event_date,
                end_date=event_date,
                event_name=event_name,
            )
            session.add(dept_log)
            session.flush()

            # 6. Department_logs entry
            department = session.query(Department).filter_by(name=department_name).one()
            attendants_count = session.query(func.count(MemberLog.id)).filter_by(log_id=member_log.id).scalar()

            dept_log_entry = DepartmentLog(
                department_id=department.id,
                log_id=dept_log.id,
                attendants_number=attendants_count,
            )
            session.add(dept_log_entry)

        print("Transaction committed successfully!")

    except Exception as e:
        session.rollback()
        print("❌ Transaction failed:", e)
    finally:
        session.close()

def add_member_event(event_name, event_date, member_action_name, members: list):
    print("Adding department event to DB...")
    try:
        with session.begin():
            # 1. find the member action (must exist)
            member_action = session.query(Action).filter_by(name=member_action_name).one()

            # 2. create a new member log for this event
            member_log = Log(
                action_id=member_action.id,
                start_date=event_date,
                end_date=event_date,
                event_name=event_name,
            )
            session.add(member_log)
            session.flush()  # ensure member_log.id is populated

            # 3. insert members (if they don't exist) and collect their ids
            member_ids = []
            for name, email, phone, uni_id in members:
                member = session.query(Member).filter_by(uni_id=uni_id).one_or_none()
                if not member:
                    member = Member(name=name, email=email, phone_number=phone, uni_id=uni_id)
                    session.add(member)
                    session.flush()  # make sure member.id is available
                member_ids.append(member.id)

            # 4. create MemberLog entries linking each member to the new log
            member_log_objs = [MemberLog(member_id=m_id, log_id=member_log.id) for m_id in member_ids]
            session.add_all(member_log_objs)

        print("Transaction committed successfully!")
        
    except Exception as e:
        session.rollback()
        print("❌ Transaction failed:", e)
    finally:
        session.close()


if __name__ == "__main__":
    main()