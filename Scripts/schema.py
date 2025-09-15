from sqlalchemy import Column, Integer, String, ForeignKey, Date
from sqlalchemy.orm import declarative_base
Base = declarative_base()
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