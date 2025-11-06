from sqlalchemy.orm import Session
from sqlalchemy import select
from .schema import Actions

def get_actions(session: Session):
    statement = select(Actions)
    actions = session.scalars(statement).all()
    session.flush()
    return actions


def get_action_by_id(session: Session, action_id: int):
    statement = select(Actions).where(Actions.id == action_id)
    action = session.scalars(statement).first()
    session.flush()
    return action