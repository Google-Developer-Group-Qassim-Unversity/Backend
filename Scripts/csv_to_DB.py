from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Date, func
from sqlalchemy.orm import declarative_base, sessionmaker

import csv
import datetime
import time
from dotenv import load_dotenv
from os import getenv
from pprint import pprint

Base = declarative_base()

# --- Table Models ---
class Action(Base):
    __tablename__ = "actions"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True)

class Log(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True)
    action_id = Column(Integer, ForeignKey("actions.id"))
    start_date = Column(Date)
    end_date = Column(Date)
    event_name = Column(String(255))

class Member(Base):
    __tablename__ = "members"
    id = Column(Integer, primary_key=True)
    name = Column(String(255))
    email = Column(String(255))
    phone_number = Column(String(50))
    uni_id = Column(Integer, unique=True)

class MemberLog(Base):
    __tablename__ = "members_logs"
    id = Column(Integer, primary_key=True)
    member_id = Column(Integer, ForeignKey("members.id"))
    log_id = Column(Integer, ForeignKey("logs.id"))

class Department(Base):
    __tablename__ = "departments"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True)

class DepartmentLog(Base):
    __tablename__ = "departments_logs"
    id = Column(Integer, primary_key=True)
    department_id = Column(Integer, ForeignKey("departments.id"))
    log_id = Column(Integer, ForeignKey("logs.id"))
    attendants_number = Column(Integer)


def main():

    department = "Tech and Business"
    department_action = "On-site course"
    event_name = "مبادئ كرة القدم"
    event_date = datetime.date(2025, 9, 12)

    # add_event(
    #     department,
    #     department_action, 
    #     event_name, 
    #     event_date,
    #     "On-site course attendance",
    #     [
    #         ("طارق فهد إبراهيم", "tariq.s@example.com", "+966 250 123 4567", 9446897735),
    #         ("فهد سعيد سلمان الغامدي", "fahad.s@example.com", "+966 255 987 6543", 9458334353),
    #         ("محمد محمد عبدالله السبيعي", "mohammed.m@example.com", "+966 253 456 7890", 9451096611),
    #         ("حمد محمد صالح الغامدي", "hamad.g@example.com", "+966 256 321 9876", 9454286931),
    #         ("خالد محمد يوسف الأنصاري", "khalid.a@example.com", "+966 254 789 1234", 9449797690),
    #         # ("سعيد علي عبدالعزيز المطيري", "saeed.m@example.com", "+966 250 654 3210", 9440757878),
    #         # ("ماجد سعيد ناصر السبيعي", "majid.s@example.com", "+966 255 321 4567", 9467145074),
    #         # ("فهد سعيد سلمان المطيري", "fahad.m@example.com", "+966 253 987 1234", 9453387821),
    #         # ("يوسف ناصر ناصر الشريف", "yousef.s@example.com", "+966 256 123 7890", 9453518462),
    #         # ("خالد إبراهيم أحمد السبيعي", "khalid.s@example.com", "+966 254 456 9871", 9453214540),
    #     ]
    # )

    result = add_member_to_existing_event(
        event_name,
        event_date,
        "Delivering a full course",
        [("حمد محمد صالح الغامدي", "hamad.g@example.com", "+966 256 321 9876", 9454286931)]
    )
    pprint(result)

    pass

def load_csv():
    with open("./downloads/Main_sheet.csv", "r", newline='', encoding="utf-8") as f:
        data = [row for row in csv.DictReader(f)]
    pprint(data)

load_dotenv()
engine = create_engine(getenv("DATABASE_URL"))
Session = sessionmaker(bind=engine)
session = Session()

def test_connection():
    start = time.time()
    print(f"Querying DB...")
    result = session.query(Action).all()
    print("Done ✅")
    end = time.time()
    for action in result:
        print(action.id, action.name)
    print(f"Query Took {end - start}ms ⌛")

def add_event(department_name, department_action_name, event_name, event_date, member_action_name, members):

    try:
        with session.begin():

            # 1. Get member action
            member_action = session.query(Action).filter_by(name=member_action_name).one()

            # 2. Create member log entry
            member_log = Log(
                action_id=member_action.id,
                start_date=event_date,
                end_date=event_date,
                event_name=event_name,
            )
            session.add(member_log)
            session.flush()  # ensures we get member_log.id

            # 3. Insert members (skip duplicates)
            member_ids = []
            for name, email, phone, uni_id in members:
                member = session.query(Member).filter_by(uni_id=uni_id).one_or_none()
                if not member:
                    member = Member(name=name, email=email, phone_number=phone, uni_id=uni_id)
                    session.add(member)
                    session.flush()
                member_ids.append(member.id)

            # 4. Insert members_logs
            for m_id in member_ids:
                session.add(MemberLog(member_id=m_id, log_id=member_log.id))

            # 5. Department log
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

        # Commit happens automatically because of session.begin()
        print("Transaction committed successfully!")

    except Exception as e:
        session.rollback()
        print("Transaction failed:", e)
    finally:
        session.close()

def add_member_to_existing_event(event_name, event_date, member_action_name, members: list):
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

        # transaction committed automatically
        return {
            'member_log_id': member_log.id,
            'added_member_ids': member_ids,
            'total_attached': len(member_ids),
        }

    except Exception as e:
        session.rollback()
        # raise or return error info depending on how you prefer to handle it
        print("Transaction failed:", e)
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()