"""First-class bonuses API (replaces fake events + /custom/* for Admin).

Standalone bonuses (``event_id = None``) and event-attached bonuses
(``event_id = <real event>``) share one model: ``bonuses`` -> ``bonus_grants``
-> ``bonus_grant_recipients``.
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.DB import (
    actions as actions_queries,
    bonuses as bonuses_queries,
    departments as departments_queries,
    events as events_queries,
    members as members_queries,
)
from app.DB.schema import Actions, Bonuses, BonusGrants, BonusRecipientType
from app.dependencies import DB
from app.helpers import CurrentMember
from app.leaderboard_cache import refresh_attendance_points_cache
from app.routers.models import BaseClassModel
from app.routers.responses import MessageResponse
from app.semesters import CLUB_TIMEZONE, resolve_semester, semester_for_event
from app.services.permissions.catalogue import Perm
from app.services.permissions.guards import Require
from app.services.push import delivery as push_delivery
from app.services.push import triggers as push_triggers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bonuses", tags=["bonuses"], dependencies=[Depends(Require(Perm.POINTS_CUSTOM))])


# ============ request / response models ============


class BonusGrantInput(BaseClassModel):
    """One grant line inside a create/update bonus payload.

    Pass ``id`` on update to modify an existing grant in place (preserving
    ``given_at`` for staying recipients and push deduplication keys).
    """

    id: str | None = None
    recipient_type: Literal["member", "department"]
    points: int
    source_action_id: int | None = None
    label_en: str | None = Field(default=None, max_length=150)
    label_ar: str | None = Field(default=None, max_length=150)
    member_ids: list[int] = Field(default_factory=list)
    department_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_recipients(self) -> "BonusGrantInput":
        if self.recipient_type == "member":
            if not self.member_ids:
                raise ValueError("member_ids must not be empty for a member grant")
            if self.department_ids:
                raise ValueError("department_ids must be empty for a member grant")
        else:
            if not self.department_ids:
                raise ValueError("department_ids must not be empty for a department grant")
            if self.member_ids:
                raise ValueError("member_ids must be empty for a department grant")
        return self


def _normalize_local_datetime(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(CLUB_TIMEZONE).replace(tzinfo=None)


def _strip_required_title(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("title must not be empty")
    return stripped


class CreateBonusRequest(BaseClassModel):
    title: str = Field(min_length=1, max_length=150)
    given_at: datetime
    is_public: bool = True
    event_id: int | None = None
    semester_id: str | None = None
    grants: list[BonusGrantInput] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        stripped = _strip_required_title(value)
        assert stripped is not None
        return stripped

    @field_validator("given_at")
    @classmethod
    def _validate_given_at(cls, value: datetime) -> datetime:
        normalized = _normalize_local_datetime(value)
        assert normalized is not None
        return normalized


class UpdateBonusRequest(BaseClassModel):
    title: str | None = Field(default=None, min_length=1, max_length=150)
    given_at: datetime | None = None
    is_public: bool | None = None
    semester_id: str | None = None
    grants: list[BonusGrantInput] | None = Field(default=None, min_length=1)

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str | None) -> str | None:
        return _strip_required_title(value)

    @field_validator("given_at")
    @classmethod
    def _validate_given_at(cls, value: datetime | None) -> datetime | None:
        return _normalize_local_datetime(value)


class UpsertEventBonusRequest(BaseClassModel):
    """Sync all bonus grants attached to a real event in one atomic call."""

    grants: list[BonusGrantInput]


class BonusGrantResponse(BaseClassModel):
    id: str
    recipient_type: Literal["member", "department"]
    points: int
    source_action_id: int | None = None
    label_en: str | None = None
    label_ar: str | None = None
    member_ids: list[int]
    department_ids: list[int]
    created_at: datetime


class BonusSummaryResponse(BaseClassModel):
    id: str
    title: str
    event_id: int | None = None
    semester_id: str
    given_at: datetime
    is_public: bool
    grant_count: int
    member_count: int
    department_count: int
    created_at: datetime


class BonusDetailResponse(BaseClassModel):
    id: str
    title: str
    event_id: int | None = None
    semester_id: str
    given_at: datetime
    is_public: bool
    created_by: int | None = None
    created_at: datetime
    updated_at: datetime
    grants: list[BonusGrantResponse]


class EventBonusResponse(BaseClassModel):
    event_id: int
    event_name: str
    start_datetime: datetime
    end_datetime: datetime
    bonus_id: str | None = None
    grants: list[BonusGrantResponse]


# ============ helpers ============


def _resolve_grant_fields(session: Session, item: BonusGrantInput) -> tuple[int, int | None, str | None, str | None]:
    """Resolve (points, source_action_id, label_en, label_ar) for a grant input.

    When ``source_action_id`` is provided, validate the template action and
    snapshot its points & bilingual labels (ignoring any conflicting manual
    points so template actions stay authoritative at creation time).
    """
    if item.source_action_id is not None:
        action: Actions | None = actions_queries.get_action_by_id(session, item.source_action_id)
        if action is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Action with id {item.source_action_id} not found"
            )
        return (
            int(action.points),
            action.id,
            (item.label_en or action.action_name).strip() or None,
            (item.label_ar or action.ar_action_name).strip() or None,
        )

    label_en = item.label_en.strip() if item.label_en and item.label_en.strip() else None
    label_ar = item.label_ar.strip() if item.label_ar and item.label_ar.strip() else None
    if label_en is not None and label_ar is None:
        label_ar = label_en
    elif label_ar is not None and label_en is None:
        label_en = label_ar
    return item.points, None, label_en, label_ar


def _validate_recipients_exist(session: Session, item: BonusGrantInput) -> None:
    if item.recipient_type == "member":
        for member_id in dict.fromkeys(item.member_ids):
            members_queries.get_member_by_id(session, member_id)
    else:
        for dept_id in dict.fromkeys(item.department_ids):
            dept = departments_queries.get_department_by_id(session, dept_id)
            if dept is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {dept_id} not found"
                )


def _sync_grants(session: Session, bonus: Bonuses, grant_inputs: list[BonusGrantInput]) -> bool:
    """Create, update, and remove grants on ``bonus``. Returns True if any push was queued."""
    existing_by_id: dict[str, BonusGrants] = {
        g.id: g for g in session.scalars(select(BonusGrants).where(BonusGrants.bonus_id == bonus.id)).all()
    }
    kept_ids: set[str] = set()
    pushed = False

    for item in grant_inputs:
        _validate_recipients_exist(session, item)
        existing = existing_by_id.get(item.id) if item.id is not None else None
        if item.id is not None and (existing is None or item.id in kept_ids):
            raise HTTPException(status_code=422, detail="Invalid or duplicate grant ID")
        if (
            existing is not None
            and item.source_action_id == existing.source_action_id
            and item.source_action_id is not None
        ):
            points = existing.points
            source_action_id = existing.source_action_id
            cleaned_en = item.label_en.strip() if item.label_en is not None else None
            cleaned_ar = item.label_ar.strip() if item.label_ar is not None else None
            label_en = cleaned_en if cleaned_en else existing.label_en
            label_ar = cleaned_ar if cleaned_ar else existing.label_ar
        else:
            points, source_action_id, label_en, label_ar = _resolve_grant_fields(session, item)
        recipient_type = BonusRecipientType(item.recipient_type)
        recipient_ids = item.member_ids if recipient_type == BonusRecipientType.MEMBER else item.department_ids

        if existing is not None:
            grant = existing
            grant.recipient_type = recipient_type
            grant.points = points
            grant.source_action_id = source_action_id
            grant.label_en = label_en
            grant.label_ar = label_ar
            session.flush()
            kept_ids.add(grant.id)
        else:
            grant = bonuses_queries.create_bonus_grant(
                session,
                bonus_id=bonus.id,
                recipient_type=recipient_type,
                points=points,
                source_action_id=source_action_id,
                label_en=label_en,
                label_ar=label_ar,
            )
            kept_ids.add(grant.id)

        added = bonuses_queries.set_grant_recipients(session, grant, recipient_ids)
        if recipient_type == BonusRecipientType.MEMBER and added:
            if push_triggers.bonus_points_given(session, bonus, grant, added) > 0:
                pushed = True

    for gid, grant in existing_by_id.items():
        if gid not in kept_ids:
            if grant in bonus.grants:
                bonus.grants.remove(grant)
            session.delete(grant)
    session.flush()
    return pushed


def _semester_id_filter(session: Session, semester: int | str) -> str:
    try:
        hijri_code = int(semester)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Semester '{semester}' not found")
    return resolve_semester(session, hijri_code).id


# ============ routes ============


@router.get("", status_code=status.HTTP_200_OK, response_model=list[BonusSummaryResponse])
def list_bonuses(
    session: DB,
    semester: Annotated[int | str, Query()] = "all",
    standalone_only: Annotated[bool, Query()] = False,
    event_id: Annotated[int | None, Query()] = None,
):
    semester_id = _semester_id_filter(session, semester) if semester != "all" else None
    bonuses = bonuses_queries.list_bonuses(
        session, semester_id=semester_id, standalone_only=standalone_only, event_id=event_id
    )
    return [BonusSummaryResponse.model_validate(bonuses_queries.serialize_bonus_summary(b)) for b in bonuses]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=BonusDetailResponse)
def create_bonus(body: CreateBonusRequest, session: DB, member: CurrentMember, background_tasks: BackgroundTasks):
    event = None
    if body.event_id is not None:
        event = events_queries.get_event_by_id(session, body.event_id, for_update=True)
        semester_id = event.semester_id
        title = event.name
        given_at = event.start_datetime
    else:
        semester = semester_for_event(session, body.given_at, body.semester_id)
        semester_id = semester.id
        title = body.title.strip()
        given_at = body.given_at

    bonus = bonuses_queries.create_bonus(
        session,
        title=title,
        semester_id=semester_id,
        given_at=given_at,
        is_public=body.is_public if event is None else True,
        event_id=event.id if event is not None else None,
        created_by=member.id,
    )
    pushed = _sync_grants(session, bonus, body.grants)
    session.commit()
    refresh_attendance_points_cache()
    if pushed:
        background_tasks.add_task(push_delivery.kick)

    reloaded = bonuses_queries.get_bonus_by_id(session, bonus.id)
    assert reloaded is not None
    return BonusDetailResponse.model_validate(bonuses_queries.serialize_bonus_detail(reloaded))


@router.get("/by-event/{event_id:int}", status_code=status.HTTP_200_OK, response_model=EventBonusResponse)
def get_event_bonuses(event_id: int, session: DB):
    event = events_queries.get_event_by_id(session, event_id)
    bonuses = bonuses_queries.get_bonuses_by_event_id(session, event_id)
    grants = [bonuses_queries.serialize_grant(g) for g in bonuses_queries.collect_grants(bonuses)]
    return EventBonusResponse(
        event_id=event.id,
        event_name=event.name,
        start_datetime=event.start_datetime,
        end_datetime=event.end_datetime,
        bonus_id=bonuses[0].id if bonuses else None,
        grants=[BonusGrantResponse.model_validate(g) for g in grants],
    )


@router.put("/by-event/{event_id:int}", status_code=status.HTTP_200_OK, response_model=EventBonusResponse)
def upsert_event_bonuses(
    event_id: int, body: UpsertEventBonusRequest, session: DB, member: CurrentMember, background_tasks: BackgroundTasks
):
    event = events_queries.get_event_by_id(session, event_id, for_update=True)
    containers = bonuses_queries.get_bonuses_by_event_id(session, event_id)
    if body.grants and not containers:
        containers = [
            bonuses_queries.create_bonus(
                session,
                title=event.name,
                semester_id=event.semester_id,
                given_at=event.start_datetime,
                is_public=True,
                event_id=event.id,
                created_by=member.id,
            )
        ]

    # Keep each grant in its original container. Moving grants and deleting
    # their containers would cascade-delete push history.
    container_by_grant = {grant.id: bonus.id for bonus in containers for grant in bonus.grants}
    inputs_by_container: dict[str, list[BonusGrantInput]] = defaultdict(list)
    for item in body.grants:
        if item.id is not None and item.id not in container_by_grant:
            raise HTTPException(status_code=422, detail="Invalid grant ID for this event")
        container_id = container_by_grant[item.id] if item.id is not None else containers[0].id
        inputs_by_container[container_id].append(item)

    pushed = False
    for bonus in containers:
        inputs = inputs_by_container[bonus.id]
        if inputs:
            pushed = _sync_grants(session, bonus, inputs) or pushed
        else:
            session.delete(bonus)
    session.commit()
    refresh_attendance_points_cache()
    if pushed:
        background_tasks.add_task(push_delivery.kick)

    return get_event_bonuses(event_id, session)


@router.get("/{bonus_id}", status_code=status.HTTP_200_OK, response_model=BonusDetailResponse)
def get_bonus(bonus_id: str, session: DB):
    bonus = bonuses_queries.get_bonus_by_id(session, bonus_id)
    if bonus is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Bonus with id {bonus_id} not found")
    return BonusDetailResponse.model_validate(bonuses_queries.serialize_bonus_detail(bonus))


@router.put("/{bonus_id}", status_code=status.HTTP_200_OK, response_model=BonusDetailResponse)
def update_bonus(bonus_id: str, body: UpdateBonusRequest, session: DB, background_tasks: BackgroundTasks):
    bonus = bonuses_queries.get_bonus_by_id(session, bonus_id)
    if bonus is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Bonus with id {bonus_id} not found")

    if body.title is not None and bonus.event_id is None:
        bonus.title = body.title.strip()
    if body.is_public is not None and bonus.event_id is None:
        bonus.is_public = 1 if body.is_public else 0
    if body.given_at is not None and bonus.event_id is None and body.given_at != bonus.given_at:
        if body.given_at.date() != bonus.given_at.date():
            bonus.legacy_end_datetime = None
            if body.semester_id is None:
                bonus.semester_id = semester_for_event(session, body.given_at).id
        bonus.given_at = body.given_at
    if body.semester_id is not None and bonus.event_id is None:
        bonus.semester_id = semester_for_event(session, bonus.given_at, body.semester_id).id
    session.flush()

    pushed = False
    if body.grants is not None:
        pushed = _sync_grants(session, bonus, body.grants)

    session.commit()
    refresh_attendance_points_cache()
    if pushed:
        background_tasks.add_task(push_delivery.kick)

    reloaded = bonuses_queries.get_bonus_by_id(session, bonus.id)
    assert reloaded is not None
    return BonusDetailResponse.model_validate(bonuses_queries.serialize_bonus_detail(reloaded))


@router.delete("/{bonus_id}", status_code=status.HTTP_200_OK, response_model=MessageResponse)
def delete_bonus(bonus_id: str, session: DB):
    deleted = bonuses_queries.delete_bonus(session, bonus_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Bonus with id {bonus_id} not found")
    session.commit()
    refresh_attendance_points_cache()
    return {"message": "Bonus deleted successfully"}
