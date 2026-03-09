from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, func, delete, update
from .schema import Actions, Logs


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
    max_order_stmt = select(func.max(Actions.order))
    max_order = session.scalar(max_order_stmt) or -1
    
    is_hidden = (type == "bonus")
    
    new_action = Actions(
        action_name=name,
        points=points,
        action_type=type,
        ar_action_name=name,
        order=max_order + 1,
        is_hidden=is_hidden,
    )
    session.add(new_action)
    session.flush()
    return new_action


def get_bonus_action(session: Session):
    statement = select(Actions).where(Actions.action_name == "Bonus")
    action = session.scalars(statement).first()
    if not action:
        new_action = create_action(session, "Bonus", 0, "bonus")
        session.flush()
        return new_action
    session.flush()
    return action


def get_discount_action(session: Session):
    statement = select(Actions).where(Actions.action_name == "Discount")
    action = session.scalars(statement).first()
    if not action:
        new_action = create_action(session, "Discount", 0, "bonus")
        session.flush()
        return new_action
    session.flush()
    return action


def get_all_actions(session: Session):
    statement = select(Actions).order_by(Actions.order)
    actions = session.scalars(statement).all()
    session.flush()
    return actions


def get_action_usage_counts(session: Session) -> dict[int, int]:
    statement = select(Logs.action_id, func.count(Logs.id)).group_by(Logs.action_id)
    results = session.execute(statement).all()
    return {row[0]: row[1] for row in results}


def update_action(
    session: Session,
    action_id: int,
    action_name: Optional[str] = None,
    points: Optional[int] = None,
    action_type: Optional[str] = None,
    ar_action_name: Optional[str] = None,
    is_hidden: Optional[bool] = None,
):
    statement = select(Actions).where(Actions.id == action_id)
    action = session.scalars(statement).first()
    if not action:
        return None
    if action_name is not None:
        action.action_name = action_name
    if points is not None:
        action.points = points
    if action_type is not None:
        action.action_type = action_type
    if ar_action_name is not None:
        action.ar_action_name = ar_action_name
    if is_hidden is not None:
        action.is_hidden = is_hidden
    session.flush()
    return action


def update_actions_order(session: Session, action_orders: list[dict]):
    for item in action_orders:
        statement = select(Actions).where(Actions.id == item["id"])
        action = session.scalars(statement).first()
        if action:
            action.order = item["order"]
    session.flush()


def get_action_usage_count(session: Session, action_id: int) -> int:
    statement = select(func.count(Logs.id)).where(Logs.action_id == action_id)
    return session.scalar(statement) or 0


def update_logs_action(session: Session, old_action_id: int, new_action_id: int):
    statement = update(Logs).where(Logs.action_id == old_action_id).values(action_id=new_action_id)
    session.execute(statement)
    session.flush()


def delete_action_by_id(session: Session, action_id: int):
    statement = delete(Actions).where(Actions.id == action_id)
    session.execute(statement)
    session.flush()
