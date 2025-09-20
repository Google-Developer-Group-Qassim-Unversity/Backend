from sqlalchemy import create_engine, Integer, Date, ForeignKey 
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column, relationship
from os import getenv
from dotenv import load_dotenv
from datetime import date
import datetime as dt

load_dotenv()
engine = create_engine(getenv("DATABASE_URL"))


class Base(DeclarativeBase):
    pass

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

from sqlalchemy import String, Integer, Date, ForeignKey, Enum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Members(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str | None] = mapped_column(String(100), unique=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), unique=True)
    uni_id: Mapped[str] = mapped_column(String(50), unique=True)
    gender: Mapped[str] = mapped_column(Enum("Male", "Female"))

    logs: Mapped[list["MembersLogs"]] = relationship(back_populates="member")


class Departments(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)

    logs: Mapped[list["DepartmentsLogs"]] = relationship(back_populates="department")


class Logs(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[int] = mapped_column(ForeignKey("actions.id", ondelete="CASCADE", onupdate="CASCADE"))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))

    action: Mapped["Actions"] = relationship(back_populates="logs")
    event: Mapped["Events"] = relationship(back_populates="logs")
    members: Mapped[list["MembersLogs"]] = relationship(back_populates="log")
    departments: Mapped[list["DepartmentsLogs"]] = relationship(back_populates="log")
    modifications: Mapped[list["Modifications"]] = relationship(back_populates="log")


class DepartmentsLogs(Base):
    __tablename__ = "departments_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id", ondelete="CASCADE", onupdate="CASCADE"))
    mf: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attendants_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("logs.id", ondelete="CASCADE", onupdate="CASCADE"))

    department: Mapped["Departments"] = relationship(back_populates="logs")
    log: Mapped["Logs"] = relationship(back_populates="departments")


class MembersLogs(Base):
    __tablename__ = "members_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    log_id: Mapped[int] = mapped_column(ForeignKey("logs.id", ondelete="CASCADE", onupdate="CASCADE"))

    member: Mapped["Members"] = relationship(back_populates="logs")
    log: Mapped["Logs"] = relationship(back_populates="members")
    absence: Mapped[list["Absence"]] = relationship(back_populates="member_log")

class Absence(Base):
    __tablename__ = "absence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    member_log_id: Mapped[int] = mapped_column(ForeignKey("members_logs.id", ondelete="CASCADE", onupdate="CASCADE"))

    member_log: Mapped["MembersLogs"] = relationship(back_populates="absence")

class Actions(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_name: Mapped[str] = mapped_column(String(60), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(Enum("composite", "department", "member"))
    action_description: Mapped[str] = mapped_column(String(100), nullable=False)
    arabic_action_name: Mapped[str] = mapped_column(String(100), nullable=False)

    logs: Mapped[list["Logs"]] = relationship(back_populates="action")


class Events(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150))

    logs: Mapped[list["Logs"]] = relationship(back_populates="event")


class Modifications(Base):
    __tablename__ = "modifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("logs.id", ondelete="CASCADE", onupdate="CASCADE"))
    type: Mapped[str] = mapped_column(Enum("bonus", "discount"), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)

    log: Mapped["Logs"] = relationship(back_populates="modifications")