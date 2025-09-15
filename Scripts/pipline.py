from dotenv import load_dotenv
from os import getenv
from sqlalchemy import Table, create_engine, select 
from sqlalchemy.orm import Session, DeclarativeBase


load_dotenv()
engine = create_engine(getenv("DATABASE_URL"))


class Base(DeclarativeBase):
    pass


class Logs(Base):
    __table__ = Table(
        "logs",
        Base.metadata,
        autoload_with=engine
    )


with Session(engine) as session:
    stmt = select(Logs.__table__)
    result = session.execute(stmt).all()
    for row in result:
        print(dict(row._mapping))
