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

def create_action(session: Session, name: str, points: int, type: str):
    new_action = Actions(
        action_name=name,
        points=points,
        action_type=type,
        ar_action_name=name,
    )
    session.add(new_action)
    session.flush()
    return new_action

def get_bonus_action(session: Session):
    statement = select(Actions).where(Actions.action_name == "Bonus")
    action = session.scalars(statement).first()
    if not action:
        new_action = create_action(session, "Bonus", 0, "composite")
        session.flush()
        return new_action
    session.flush()
    return action

def get_discount_action(session: Session):
    statement = select(Actions).where(Actions.action_name == "Discount")
    action = session.scalars(statement).first()
    if not action:
        new_action = create_action(session, "Discount", 0, "composite")
        session.flush()
        return new_action
    session.flush()
    return action