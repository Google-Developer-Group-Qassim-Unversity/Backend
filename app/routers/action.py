from typing import Optional, Annotated
from fastapi import APIRouter, Depends, status, HTTPException, Query
from app.DB import actions as actions_queries

from app.dependencies import DB

from app.routers.models import (
    Categorized_action,
    CreateAction_model,
    UpdateAction_model,
    Action_model,
    ActionWithUsage_model,
    ReorderActions_model,
)

from app.routers.responses import MessageResponse
from app.services.permissions.catalogue import Perm
from app.services.permissions.guards import Require


router = APIRouter(prefix="/actions", tags=["actions"])


@router.get("", status_code=status.HTTP_200_OK, response_model=Categorized_action)
def get_categorized_actions(session: DB):
    actions_queries.get_bonus_action(session)
    actions_queries.get_discount_action(session)
    session.commit()

    actions = actions_queries.get_actions(session)

    categorized_action = {"composite_actions": [], "department_actions": [], "member_actions": [], "custom_actions": []}

    # 1. Add composite actions: the points tiers, hardcoded production ids (see
    # COMPOSITE_ACTION_IDS and docs/HARDCODED_ACTION_IDS.md).
    categorized_action["composite_actions"] = actions_queries.get_composite_pairs(session)

    # 2. filter out department and member actions used in composites
    paired = {action_id for pair in actions_queries.COMPOSITE_ACTION_IDS for action_id in pair}
    actions = [action for action in actions if action.id not in paired]

    # 3. add department and member actions
    categorized_action["department_actions"] = [action for action in actions if action.action_type == "department"]
    categorized_action["member_actions"] = [action for action in actions if action.action_type == "member"]

    # 4. add custom actions (all bonus-type actions)
    categorized_action["custom_actions"] = [action for action in actions if action.action_type == "bonus"]

    return Categorized_action(
        composite_actions=categorized_action["composite_actions"],
        department_actions=categorized_action["department_actions"],
        member_actions=categorized_action["member_actions"],
        custom_actions=categorized_action["custom_actions"],
    )


@router.get("/all", status_code=status.HTTP_200_OK, response_model=list[ActionWithUsage_model])
def get_all_actions(session: DB):
    actions = actions_queries.get_all_actions(session)
    usage_counts = actions_queries.get_action_usage_counts(session)
    return [
        ActionWithUsage_model(
            id=action.id,
            action_name=action.action_name,
            ar_action_name=action.ar_action_name,
            action_type=action.action_type.value,
            points=action.points,
            usage_count=usage_counts.get(action.id, 0),
            order=action.order,
            is_hidden=bool(action.is_hidden),
        )
        for action in actions
    ]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Action_model,
    dependencies=[Depends(Require(Perm.POINTS_CATALOGUE))],
)
def create_action(payload: CreateAction_model, session: DB):
    new_action = actions_queries.create_action(
        session, name=payload.action_name, points=payload.points, type=payload.action_type
    )
    new_action.ar_action_name = payload.ar_action_name
    session.commit()
    session.refresh(new_action)
    return new_action


@router.put(
    "/{action_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Action_model,
    dependencies=[Depends(Require(Perm.POINTS_CATALOGUE))],
)
def update_action(action_id: int, payload: UpdateAction_model, session: DB):
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


@router.put(
    "/reorder",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(Require(Perm.POINTS_CATALOGUE))],
    response_model=MessageResponse,
)
def reorder_actions(payload: ReorderActions_model, session: DB):
    actions_queries.update_actions_order(session, payload.action_orders)
    session.commit()
    return {"message": "Actions reordered successfully"}


@router.delete(
    "/{action_id:int}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(Require(Perm.POINTS_CATALOGUE))],
    response_model=MessageResponse,
)
def delete_action(action_id: int, session: DB, replacement_id: Annotated[Optional[int], Query()] = None):
    action = actions_queries.get_action_by_id(session, action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    usage_count = actions_queries.get_action_usage_count(session, action_id)

    if usage_count > 0 and replacement_id is None:
        raise HTTPException(status_code=400, detail="Must provide replacement_id when action has been used")

    if replacement_id:
        replacement = actions_queries.get_action_by_id(session, replacement_id)
        if not replacement:
            raise HTTPException(status_code=404, detail="Replacement action not found")

        actions_queries.update_logs_action(session, action_id, replacement_id)

    actions_queries.delete_action_by_id(session, action_id)
    session.commit()

    return {"message": "Action deleted successfully"}
