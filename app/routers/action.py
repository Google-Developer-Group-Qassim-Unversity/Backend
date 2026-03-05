from typing import Literal, Optional
from fastapi import APIRouter, status, HTTPException, Query
from app.DB import actions as actions_queries
from ..DB.main import SessionLocal
from app.routers.models import (
    Categorized_action,
    CreateAction_model,
    UpdateAction_model,
    Action_model,
    ActionWithUsage_model,
    ReorderActions_model,
)

router = APIRouter()


def get_action_by_id(actions, action_id: int):
    for action in actions:
        if action.id == action_id:
            return action
    return None


@router.get("", status_code=status.HTTP_200_OK, response_model=Categorized_action)
def get_categorized_actions():
    # These are to link department and member actions into composite actions
    department_ids = [51, 52, 53, 54, 86, 88, 90]
    member_ids = [76, 77, 78, 79, 87, 89, 91]

    with SessionLocal() as session:
        actions = actions_queries.get_actions(session)

    categorized_action = {
        "composite_actions": [],
        "department_actions": [],
        "member_actions": [],
        "custom_actions": [],
    }

    # 1. Add composite actions (only include pairs where both actions exist)
    for deptId, memberId in zip(department_ids, member_ids):
        dept_action = get_action_by_id(actions, deptId)
        member_action = get_action_by_id(actions, memberId)
        if dept_action is not None and member_action is not None:
            categorized_action["composite_actions"].append((dept_action, member_action))

    # 2. filter out department and member actions used in composites
    actions = [
        action for action in actions if action.id not in department_ids + member_ids
    ]

    # 3. add department and member actions
    categorized_action["department_actions"] = [
        action for action in actions if action.action_type == "department"
    ]
    categorized_action["member_actions"] = [
        action for action in actions if action.action_type == "member"
    ]

    # 4. add custom actions (all bonus-type actions)
    categorized_action["custom_actions"] = [
        action for action in actions if action.action_type == "bonus"
    ]

    return Categorized_action(
        composite_actions=categorized_action["composite_actions"],
        department_actions=categorized_action["department_actions"],
        member_actions=categorized_action["member_actions"],
        custom_actions=categorized_action["custom_actions"],
    )


@router.get(
    "/all", status_code=status.HTTP_200_OK, response_model=list[ActionWithUsage_model]
)
def get_all_actions():
    with SessionLocal() as session:
        actions = actions_queries.get_all_actions(session)
        usage_counts = actions_queries.get_action_usage_counts(session)
    return [
        ActionWithUsage_model(
            id=action.id,
            action_name=action.action_name,
            ar_action_name=action.ar_action_name,
            action_type=action.action_type,
            points=action.points,
            usage_count=usage_counts.get(action.id, 0),
            order=action.order,
            is_hidden=bool(action.is_hidden),
        )
        for action in actions
    ]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Action_model)
def create_action(payload: CreateAction_model):
    with SessionLocal() as session:
        new_action = actions_queries.create_action(
            session,
            name=payload.action_name,
            points=payload.points,
            type=payload.action_type,
        )
        new_action.ar_action_name = payload.ar_action_name
        session.commit()
        session.refresh(new_action)
    return new_action


@router.put("/{action_id:int}", status_code=status.HTTP_200_OK, response_model=Action_model)
def update_action(action_id: int, payload: UpdateAction_model):
    with SessionLocal() as session:
        updated_action = actions_queries.update_action(
            session,
            action_id=action_id,
            action_name=payload.action_name,
            points=payload.points,
            action_type=payload.action_type,
            ar_action_name=payload.ar_action_name,
            is_hidden=payload.is_hidden,
        )
        if not updated_action:
            raise HTTPException(status_code=404, detail="Action not found")
        session.commit()
        session.refresh(updated_action)
    return updated_action


@router.put("/reorder", status_code=status.HTTP_200_OK)
def reorder_actions(payload: ReorderActions_model):
    with SessionLocal() as session:
        actions_queries.update_actions_order(session, payload.action_orders)
        session.commit()
    return {"message": "Actions reordered successfully"}


@router.delete("/{action_id:int}", status_code=status.HTTP_200_OK)
def delete_action(action_id: int, replacement_id: Optional[int] = Query(None)):
    with SessionLocal() as session:
        action = actions_queries.get_action_by_id(session, action_id)
        if not action:
            raise HTTPException(status_code=404, detail="Action not found")
        
        usage_count = actions_queries.get_action_usage_count(session, action_id)
        
        if usage_count > 0 and replacement_id is None:
            raise HTTPException(
                status_code=400, 
                detail="Must provide replacement_id when action has been used"
            )
        
        if replacement_id:
            replacement = actions_queries.get_action_by_id(session, replacement_id)
            if not replacement:
                raise HTTPException(status_code=404, detail="Replacement action not found")
            
            actions_queries.update_logs_action(session, action_id, replacement_id)
        
        actions_queries.delete_action_by_id(session, action_id)
        session.commit()
    
    return {"message": "Action deleted successfully"}
