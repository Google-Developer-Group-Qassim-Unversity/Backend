"""Compatibility shim for legacy ``/custom/*`` endpoints, backed by ``bonuses``.

New clients (Admin web) use ``/bonuses`` directly. This router stays alive so
shipped TestFlight iOS builds (whose admin portal calls ``POST /custom/members``
and ``GET /custom/members/{event_id}``) keep working without creating fake
``events`` rows. Remove once old TestFlight builds are retired.
"""

import logging
from datetime import datetime
from typing import List, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.DB import (
    actions as actions_queries,
    bonuses as bonuses_queries,
    departments as departments_queries,
    events as events_queries,
    members as members_queries,
)
from app.DB.schema import BonusRecipientType
from app.dependencies import DB
from app.helpers import CurrentMember
from app.leaderboard_cache import refresh_attendance_points_cache
from app.routers.models import BaseClassModel
from app.routers.responses import CustomPointsCreatedResponse, MessageResponse
from app.semesters import semester_for_event
from app.services.permissions.catalogue import Perm
from app.services.permissions.guards import Require
from app.services.push import delivery as push_delivery
from app.services.push import triggers as push_triggers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom", tags=["custom"], dependencies=[Depends(Require(Perm.POINTS_CUSTOM))])


class DepartmentPointDetails(BaseClassModel):
    departments_id: list[int]
    points: int
    action_id: int | None = None
    action_name: str | None = None


class MemberPointDetails(BaseClassModel):
    member_ids: list[int]
    points: int
    action_id: int | None = None
    action_name: str | None = None


class CustomDepartmentPointsRequest(BaseClassModel):
    event_id: int | None = None
    start_datetime: datetime
    end_datetime: datetime
    event_name: str
    location_type: Literal["online", "on-site", "none", "hidden"] = "none"
    point_deatils: List[DepartmentPointDetails]


class CustomMemberPointsRequest(BaseClassModel):
    event_id: int | None = None
    start_datetime: datetime
    end_datetime: datetime
    event_name: str
    location_type: Literal["online", "on-site", "none", "hidden"] = "none"
    point_deatils: List[MemberPointDetails]


class DepartmentPointDetailsWithLogId(BaseClassModel):
    log_id: int
    departments_id: list[int]
    points: int
    action_id: int | None = None
    action_name: str | None = None


class MemberPointDetailsWithLogId(BaseClassModel):
    log_id: int
    member_ids: list[int]
    points: int
    action_id: int | None = None
    action_name: str | None = None


class CustomDepartmentPointsResponse(BaseClassModel):
    event_id: int
    start_datetime: datetime
    end_datetime: datetime
    event_name: str
    point_details: List[DepartmentPointDetailsWithLogId]


class CustomMemberPointsResponse(BaseClassModel):
    event_id: int
    start_datetime: datetime
    end_datetime: datetime
    event_name: str
    point_details: List[MemberPointDetailsWithLogId]


def _resolve_legacy_grant(
    session: DB, action_id: int | None, action_name: str | None, points: int
) -> tuple[int, int | None, str | None, str | None]:
    if action_id is not None:
        action = actions_queries.get_action_by_id(session, action_id)
        if action is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Action with id {action_id} not found")
        return int(action.points), action.id, action.action_name, action.ar_action_name
    if action_name and action_name.strip():
        clean = action_name.strip()
        return points, None, clean, clean
    return points, None, None, None


def _get_or_create_target_bonus(
    session: DB,
    *,
    event_id: int | None,
    event_name: str,
    start_datetime: datetime,
    end_datetime: datetime,
    location_type: str,
    actor_id: int,
):
    if event_id:
        legacy = bonuses_queries.get_standalone_bonus_by_legacy_id(session, event_id)
        if legacy is not None:
            return legacy, legacy.legacy_id
        event = events_queries.get_event_by_id(session, event_id, for_update=True)
        bonus = bonuses_queries.get_bonus_by_event_id(session, event.id)
        if bonus is None:
            bonus = bonuses_queries.create_bonus(
                session,
                title=event.name,
                semester_id=event.semester_id,
                given_at=event.start_datetime,
                is_public=True,
                event_id=event.id,
                created_by=actor_id,
            )
        return bonus, event.id

    semester = semester_for_event(session, end_datetime)
    bonus = bonuses_queries.create_bonus(
        session,
        title=event_name.strip(),
        semester_id=semester.id,
        given_at=start_datetime,
        is_public=location_type != "hidden",
        event_id=None,
        created_by=actor_id,
        legacy_end_datetime=end_datetime,
    )
    return bonus, bonus.legacy_id


def _legacy_event_details(session: DB, event_id: int):
    standalone = bonuses_queries.get_standalone_bonus_by_legacy_id(session, event_id)
    if standalone is not None:
        return (
            standalone.title,
            standalone.given_at,
            standalone.legacy_end_datetime or standalone.given_at,
            standalone.grants,
        )
    event = events_queries.get_event_by_id(session, event_id)
    bonuses = bonuses_queries.get_bonuses_by_event_id(session, event_id)
    return event.name, event.start_datetime, event.end_datetime, bonuses_queries.collect_grants(bonuses)


@router.post("/departments", status_code=status.HTTP_201_CREATED, response_model=CustomPointsCreatedResponse)
def give_department_custom_points(body: CustomDepartmentPointsRequest, session: DB, member: CurrentMember):
    bonus, response_event_id = _get_or_create_target_bonus(
        session,
        event_id=body.event_id,
        event_name=body.event_name,
        start_datetime=body.start_datetime,
        end_datetime=body.end_datetime,
        location_type=body.location_type,
        actor_id=member.id,
    )
    for detail in body.point_deatils:
        for dept_id in detail.departments_id:
            if departments_queries.get_department_by_id(session, dept_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {dept_id} not found"
                )
        points, source_action_id, label_en, label_ar = _resolve_legacy_grant(
            session, detail.action_id, detail.action_name, detail.points
        )
        grant = bonuses_queries.create_bonus_grant(
            session,
            bonus_id=bonus.id,
            recipient_type=BonusRecipientType.DEPARTMENT,
            points=points,
            source_action_id=source_action_id,
            label_en=label_en,
            label_ar=label_ar,
        )
        bonuses_queries.set_grant_recipients(session, grant, detail.departments_id)

    session.commit()
    refresh_attendance_points_cache()
    return {
        "event_id": response_event_id,
        "bonus_id": bonus.id,
        "message": "Successfully given custom points to departments",
    }


@router.get("/departments/{event_id:int}", response_model=CustomDepartmentPointsResponse)
def get_department_custom_points(event_id: int, session: DB):
    name, start, end, all_grants = _legacy_event_details(session, event_id)
    grants = [g for g in all_grants if g.recipient_type == BonusRecipientType.DEPARTMENT]
    return CustomDepartmentPointsResponse(
        event_id=event_id,
        start_datetime=start,
        end_datetime=end,
        event_name=name,
        point_details=[
            DepartmentPointDetailsWithLogId(
                log_id=g.legacy_id,
                departments_id=sorted(r.department_id for r in g.recipients if r.department_id is not None),
                points=g.points,
                action_id=g.source_action_id,
                action_name=g.label_en or ("Discount" if g.points < 0 else "Bonus"),
            )
            for g in grants
        ],
    )


@router.put("/departments/{log_id}", response_model=MessageResponse)
def update_department_custom_points(log_id: str, body: DepartmentPointDetails, session: DB):
    grant = bonuses_queries.get_bonus_grant_by_id(session, log_id)
    if grant is None or grant.recipient_type != BonusRecipientType.DEPARTMENT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Log with id {log_id} not found")
    for dept_id in body.departments_id:
        if departments_queries.get_department_by_id(session, dept_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {dept_id} not found")
    points, source_action_id, label_en, label_ar = _resolve_legacy_grant(
        session, body.action_id, body.action_name, body.points
    )
    if source_action_id is None or source_action_id != grant.source_action_id:
        grant.points = points
    grant.source_action_id = source_action_id
    grant.label_en = label_en
    grant.label_ar = label_ar
    bonuses_queries.set_grant_recipients(session, grant, body.departments_id)
    session.commit()
    refresh_attendance_points_cache()
    return {"message": "Successfully updated custom department points"}


@router.delete("/departments/{log_id}", response_model=MessageResponse)
def delete_department_custom_points(log_id: str, session: DB):
    grant = bonuses_queries.get_bonus_grant_by_id(session, log_id)
    if grant is None or grant.recipient_type != BonusRecipientType.DEPARTMENT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Log with id {log_id} not found")
    bonuses_queries.delete_bonus_grant(session, grant.id)
    session.commit()
    refresh_attendance_points_cache()
    return {"message": "Successfully deleted custom department points"}


@router.post("/members", status_code=status.HTTP_201_CREATED, response_model=CustomPointsCreatedResponse)
def give_member_custom_points(
    body: CustomMemberPointsRequest, session: DB, member: CurrentMember, background_tasks: BackgroundTasks
):
    bonus, response_event_id = _get_or_create_target_bonus(
        session,
        event_id=body.event_id,
        event_name=body.event_name,
        start_datetime=body.start_datetime,
        end_datetime=body.end_datetime,
        location_type=body.location_type,
        actor_id=member.id,
    )
    pushed = False
    for detail in body.point_deatils:
        for member_id in detail.member_ids:
            members_queries.get_member_by_id(session, member_id)
        points, source_action_id, label_en, label_ar = _resolve_legacy_grant(
            session, detail.action_id, detail.action_name, detail.points
        )
        grant = bonuses_queries.create_bonus_grant(
            session,
            bonus_id=bonus.id,
            recipient_type=BonusRecipientType.MEMBER,
            points=points,
            source_action_id=source_action_id,
            label_en=label_en,
            label_ar=label_ar,
        )
        bonuses_queries.set_grant_recipients(session, grant, detail.member_ids)
        if push_triggers.bonus_points_given(session, bonus, grant, detail.member_ids) > 0:
            pushed = True

    session.commit()
    refresh_attendance_points_cache()
    if pushed:
        background_tasks.add_task(push_delivery.kick)
    return {
        "event_id": response_event_id,
        "bonus_id": bonus.id,
        "message": "Successfully given custom points to members",
    }


@router.get("/members/{event_id:int}", response_model=CustomMemberPointsResponse)
def get_member_custom_points(event_id: int, session: DB):
    name, start, end, all_grants = _legacy_event_details(session, event_id)
    grants = [g for g in all_grants if g.recipient_type == BonusRecipientType.MEMBER]
    return CustomMemberPointsResponse(
        event_id=event_id,
        start_datetime=start,
        end_datetime=end,
        event_name=name,
        point_details=[
            MemberPointDetailsWithLogId(
                log_id=g.legacy_id,
                member_ids=sorted(r.member_id for r in g.recipients if r.member_id is not None),
                points=g.points,
                action_id=g.source_action_id,
                action_name=g.label_en or ("Discount" if g.points < 0 else "Bonus"),
            )
            for g in grants
        ],
    )


@router.put("/members/{log_id}", response_model=MessageResponse)
def update_member_custom_points(log_id: str, body: MemberPointDetails, session: DB, background_tasks: BackgroundTasks):
    grant = bonuses_queries.get_bonus_grant_by_id(session, log_id)
    if grant is None or grant.recipient_type != BonusRecipientType.MEMBER:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Log with id {log_id} not found")
    for mem_id in body.member_ids:
        members_queries.get_member_by_id(session, mem_id)
    points, source_action_id, label_en, label_ar = _resolve_legacy_grant(
        session, body.action_id, body.action_name, body.points
    )
    if source_action_id is None or source_action_id != grant.source_action_id:
        grant.points = points
    grant.source_action_id = source_action_id
    grant.label_en = label_en
    grant.label_ar = label_ar
    bonuses_queries.set_grant_recipients(session, grant, body.member_ids)

    pushed = False
    if grant.bonus is not None:
        if push_triggers.bonus_points_given(session, grant.bonus, grant, body.member_ids) > 0:
            pushed = True

    session.commit()
    refresh_attendance_points_cache()
    if pushed:
        background_tasks.add_task(push_delivery.kick)
    return {"message": "Successfully updated custom member points"}


@router.delete("/members/{log_id}", response_model=MessageResponse)
def delete_member_custom_points(log_id: str, session: DB):
    grant = bonuses_queries.get_bonus_grant_by_id(session, log_id)
    if grant is None or grant.recipient_type != BonusRecipientType.MEMBER:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Log with id {log_id} not found")
    bonuses_queries.delete_bonus_grant(session, grant.id)
    session.commit()
    refresh_attendance_points_cache()
    return {"message": "Successfully deleted custom member points"}
