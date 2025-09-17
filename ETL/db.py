from sqlalchemy import create_engine, Table
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from os import getenv
from dotenv import load_dotenv

load_dotenv()
engine = create_engine(getenv("DATABASE_URL"))


class Base(DeclarativeBase):
    pass

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Logs(Base):
    __table__ = Table(
        "logs",
        Base.metadata,
        autoload_with=engine
    )


class Members(Base):
    __table__ = Table(
        "members", 
        Base.metadata,
        autoload_with=engine
    )

class Departments(Base):
    __table__ = Table(
        "departments",
        Base.metadata, 
        autoload_with=engine
    )

class DepartmentsLogs(Base):
    __table__ = Table(
        "departments_logs",
        Base.metadata,
        autoload_with=engine
    )

class MembersLogs(Base):
    __table__ = Table(
        "members_logs", 
        Base.metadata,
        autoload_with=engine
    )

class Actions(Base):
    __table__ = Table(
        "actions", 
        Base.metadata,
        autoload_with=engine
    )

class Events(Base):
    __table__ = Table(
        "events", 
        Base.metadatam,
        autoload_with=engine
    )

class Modifications(Base):
    __table__ = Table(
        "modifications", 
        Base.metadata,
        autoload_with=engine
    )

class DepartmentsPoints(Base):
    __table__ = Table(
        "deparments_points",
        Base.metadata,
        autoload_with=engine
    )

    __mapper_args__ = {"primary_key": [__table__.c.id]}


class MembersPoints(Base):
    __table__ = Table(
        "members_points",
        Base.metadata, 
        autoload_with=engine
    )

    __mapper_args__ = {"primary_key": [__table__.c.id]}

class DeparmentsHistory(Base):
    __table__ = Table(
        "deparment_history",
        Base.metadata, 
        autoload_with=engine
    )

    __mapper_args__ = {"primary_key": [__table__.c.id]}


class MembersHistory(Base):
    __table__ = Table(
        "members_history",
        Base.metadata, 
        autoload_with=engine
    )

    __mapper_args__ = {"primary_key": [__table__.c.id]}
