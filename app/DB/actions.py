from sqlalchemy.orm import Session
from sqlalchemy import select
from .schema import Actions

def get_actions(session: Session):
    statement = select(Actions)
    actions = session.scalars(statement).all()
    session.flush()
    return actions


