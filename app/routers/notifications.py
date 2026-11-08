"""The member app's push notifications: phones, switches, the feed, and organizers' announcements.

The member always comes from the session (``CurrentMember``), never from a
body or a path. See docs/PUSH.md.
"""

import logging
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.config import config
from app.dependencies import DB
from app.DB import events as events_queries
from app.DB import push as push_queries
from app.DB.ids import new_id
from app.DB.schema import (
    Forms,
    MemberDevices,
    PushBroadcasts,
    PushEnvironment,
    PushKind,
    PushLocale,
    PushPlatform,
    Submissions,
    SubmissionsSubmissionType,
)
from app.exceptions import BroadcastLimitReached, PushNotConfigured, PushUrlNotAllowed
from app.helpers import CurrentMember
from app.routers.notification_models import (
    BroadcastRequest,
    BroadcastResponse,
    DeviceDeletedResponse,
    DeviceRegisteredResponse,
    DeviceRegistrationRequest,
    FeedItemResponse,
    NotificationPrefsResponse,
    NotificationPrefsUpdate,
)
from app.services.permissions.catalogue import Perm
from app.services.permissions.guards import Require
from app.services.push import cases, clock, delivery, links, messages, outbox
from app.services.push.outbox import Push

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])

FEED_DAYS = 60
BROADCASTS_PER_DAY = 3
BROADCAST_TTL = timedelta(days=2)


# ---------------------------------------------------------------- phones


@router.post("/devices", status_code=status.HTTP_200_OK, response_model=DeviceRegisteredResponse)
def register_device(
    body: DeviceRegistrationRequest, member: CurrentMember, session: DB, background_tasks: BackgroundTasks
):
    """Register (or refresh) this phone for the signed-in member. A token signed in elsewhere moves here.

    A new member's first phone gets the welcome push (``cases.first_device``).
    """
    had_device = bool(session.scalar(select(exists().where(MemberDevices.member_id == member.id))))
    device = push_queries.register_device(
        session,
        member.id,
        token=body.token,
        platform=PushPlatform(body.platform),
        environment=PushEnvironment(body.environment),
        app_version=body.app_version,
        app_build=body.app_build,
        locale=PushLocale(body.locale),
        now=clock.now(),
    )
    welcomed = cases.first_device(session, member, had_device=had_device)
    session.commit()
    logger.info("Member %s registered device %s (%s, %s)", member.id, device.id, body.environment, body.locale)
    if welcomed:
        background_tasks.add_task(delivery.kick)
    return {"id": device.id, "registered": True}


@router.delete("/devices/{device_id}", status_code=status.HTTP_200_OK, response_model=DeviceDeletedResponse)
def unregister_device(device_id: str, member: CurrentMember, session: DB):
    """Stop pushes to this phone (sign-out, or notifications turned off in iOS). Only the caller's own phone."""
    deleted = push_queries.delete_device(session, member.id, device_id)
    session.commit()
    return {"deleted": deleted}


# ---------------------------------------------------------------- switches


def _prefs_response(session: Session, member_id: int) -> dict:
    prefs = push_queries.get_prefs(session, member_id)
    return {**push_queries.prefs_as_dict(prefs), "updated_at": prefs.updated_at if prefs else None}


@router.get("/preferences", status_code=status.HTTP_200_OK, response_model=NotificationPrefsResponse)
def get_preferences(member: CurrentMember, session: DB):
    session.commit()  # the clerk_user_id self-heal in CurrentMember
    return _prefs_response(session, member.id)


@router.patch("/preferences", status_code=status.HTTP_200_OK, response_model=NotificationPrefsResponse)
def update_preferences(body: NotificationPrefsUpdate, member: CurrentMember, session: DB):
    changes = body.model_dump(exclude_none=True)
    if changes:
        push_queries.save_prefs(session, member.id, changes, clock.now())
    session.commit()
    return _prefs_response(session, member.id)


# ---------------------------------------------------------------- feed


@router.get("/me", status_code=status.HTTP_200_OK, response_model=list[FeedItemResponse])
def my_notifications(
    member: CurrentMember,
    session: DB,
    lang: Annotated[Literal["ar", "en"], Query()] = "ar",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    """What was sent to the member's phones in the last 60 days, newest first, in ``lang``."""
    session.commit()
    rows = push_queries.feed(session, member.id, clock.now() - timedelta(days=FEED_DAYS), limit)
    items = []
    for row in rows:
        text = row.payload.get(lang) or row.payload.get("ar") or {}
        items.append(
            {
                "id": row.id,
                "kind": row.kind.value,
                "title": text.get("title", ""),
                "body": text.get("body", ""),
                "url": row.payload.get("url") or links.DEFAULT_URL,
                "event_id": row.event_id,
                "created_at": row.sent_at or row.created_at,
            }
        )
    return items


# ---------------------------------------------------------------- announcements


def _audience_member_ids(session: Session, body: BroadcastRequest) -> tuple[str, set[int] | None]:
    """The switch the audience answers to, and its members (None: everyone)."""
    if body.audience.type == "all":
        return "news", None
    event = events_queries.get_event_by_id(session, body.audience.event_id)
    registrants = session.scalars(
        select(Submissions.member_id)
        .join(Forms, Forms.id == Submissions.form_id)
        .where(
            Forms.event_id == event.id,
            Submissions.is_accepted == 1,
            Submissions.cancelled_at.is_(None),
            Submissions.submission_type != SubmissionsSubmissionType.PARTIAL,
        )
    ).all()
    return "remind", set(registrants)


@router.post(
    "/broadcast",
    status_code=status.HTTP_200_OK,
    response_model=BroadcastResponse,
    dependencies=[Depends(Require(Perm.NOTIFICATIONS_BROADCAST))],
)
def send_broadcast(body: BroadcastRequest, member: CurrentMember, session: DB, background_tasks: BackgroundTasks):
    """Send an announcement to members' phones: everyone with club news on, or one event's accepted registrants.

    ``recipients`` counts only members a push reaches (the switch on and the app
    on a phone); ``dry_run`` returns that number without sending anything.
    """
    if not body.url_allowed:
        raise PushUrlNotAllowed()
    switch, audience = _audience_member_ids(session, body)
    recipients = outbox.reachable(session, switch, audience)
    if body.dry_run:
        session.commit()
        return {"broadcast_id": None, "recipients": len(recipients)}
    if not config.push_enabled:
        raise PushNotConfigured()

    now = clock.now()
    start, end = clock.riyadh_day_bounds(now)
    sent_today = session.scalar(
        select(func.count())
        .select_from(PushBroadcasts)
        .where(PushBroadcasts.created_at >= start, PushBroadcasts.created_at < end)
    )
    if (sent_today or 0) >= BROADCASTS_PER_DAY:
        raise BroadcastLimitReached(BROADCASTS_PER_DAY)

    audience_json = body.audience.model_dump()
    row = PushBroadcasts(
        id=new_id(),
        sent_by=member.id,
        title_ar=body.title_ar,
        body_ar=body.body_ar,
        title_en=body.title_en,
        body_en=body.body_en,
        url=body.url,
        audience=audience_json,
        recipient_count=len(recipients),
        created_at=now,
    )
    session.add(row)
    session.flush()
    event_id = audience_json.get("event_id")
    outbox.enqueue(
        session,
        PushKind.BROADCAST,
        [Push(member_id=member_id, dedupe_key=f"broadcast:{row.id}:{member_id}") for member_id in recipients],
        text=messages.broadcast(body.title_ar, body.body_ar, body.title_en, body.body_en),
        url=body.url or links.DEFAULT_URL,
        thread_id=f"event-{event_id}" if event_id else "news",
        switch=switch,
        event_id=event_id,
        broadcast_id=row.id,
        expires_at=now + BROADCAST_TTL,
        now=now,
        reachable_ids=recipients,
    )
    session.commit()
    logger.info("Member %s sent broadcast %s to %d member(s) (%s)", member.id, row.id, len(recipients), switch)
    background_tasks.add_task(delivery.kick)
    return {"broadcast_id": row.id, "recipients": len(recipients)}
