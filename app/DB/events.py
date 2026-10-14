import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import Select, select, func

from app.exceptions import EventNotFound
from .schema import (
    Events,
    EventsContentLinkKind,
    EventsLocationType,
    EventsStatus,
    MembersLogs,
    t_open_events,
    Logs,
    DepartmentsLogs,
    Actions,
    Departments,
    EventContentLinks,
)
from app.routers.models import Events_model
from app.config import config
from app.helpers import get_effective_date
from app.semesters import semester_for_event
from datetime import datetime, timedelta

if TYPE_CHECKING:
    from app.routers.models import CreateEventContentLink_model, UpdateEventContentLink_model

logger = logging.getLogger(__name__)


def get_events(session: Session):
    """The whole events table, newest first - the leaderboard app's full array."""
    statement = select(Events)
    statement = statement.order_by(Events.start_datetime.desc())
    events = list(session.scalars(statement).all())
    attach_published_content_links_of_all_events(session, events)
    return events


def get_open_events(session: Session):
    statement = select(t_open_events)
    results = session.execute(statement).all()
    return [dict(row._mapping) for row in results]


def get_events_by_semester(session: Session, semester_id: str):
    statement = select(Events).where(Events.semester_id == semester_id).order_by(Events.start_datetime.desc())
    events = list(session.scalars(statement).all())
    attach_published_content_links_of_semester(session, events, semester_id)
    return events


def _apply_event_filters(stmt, semester_id=None, status=None, search=None, exclude_custom=False):
    """Shared WHERE clauses for the paginated list: optional semester, status,
    and a name search. Applied to both the page query and its count."""
    if semester_id is not None:
        stmt = stmt.where(Events.semester_id == semester_id)
    if status is not None:
        stmt = stmt.where(Events.status == status)
    if search and search.strip():
        stmt = stmt.where(Events.name.like(f"%{search.strip()}%"))
    if exclude_custom:
        # "none"/"hidden" location types are custom point activities - they live
        # on the points page, not the events list.
        stmt = stmt.where(Events.location_type.notin_([EventsLocationType.NONE, EventsLocationType.HIDDEN]))
    return stmt


def count_events(session: Session, semester_id=None, status=None, search=None) -> int:
    stmt = _apply_event_filters(select(func.count()).select_from(Events), semester_id, status, search)
    return session.scalar(stmt) or 0


def get_events_paginated(
    session: Session, limit: int, offset: int, semester_id=None, status=None, search=None, exclude_custom=False
):
    stmt = _apply_event_filters(select(Events), semester_id, status, search, exclude_custom)
    # newest first, id as the deterministic tiebreaker for stable page edges.
    stmt = stmt.order_by(Events.start_datetime.desc(), Events.id.desc()).offset(offset).limit(limit)
    events = session.scalars(stmt).all()
    if not events:
        return events
    return _attach_department_and_attendance(session, events)


def _attach_department_and_attendance(session: Session, events: list[Events]) -> list[Events]:
    """Annotate one page's worth of events with their department and distinct
    attendee count. Two grouped queries for the whole page - no N+1."""
    event_ids = [event.id for event in events if event.id is not None]

    # Event department: the event's department log (first action is always the
    # department action). Tie to the per-day department_logs row ids.
    dept_stmt = (
        select(
            Logs.event_id,
            Departments.id.label("department_id"),
            Departments.name.label("department_name"),
            Departments.ar_name.label("department_ar_name"),
        )
        .select_from(Logs)
        .join(DepartmentsLogs, DepartmentsLogs.log_id == Logs.id)
        .join(Departments, Departments.id == DepartmentsLogs.department_id)
        .where(Logs.event_id.in_(event_ids))
        .group_by(Logs.event_id, Departments.id)
    )
    for row in session.execute(dept_stmt).all():
        event = next((e for e in events if e.id == row.event_id), None)
        if event and getattr(event, "department_id", None) is None:
            event.department_id = row.department_id
            event.department_name = row.department_name
            event.department_ar_name = row.department_ar_name

    # Distinct members who attended any day of the event.
    att_stmt = (
        select(Logs.event_id, func.count(func.distinct(MembersLogs.member_id)).label("attendance_count"))
        .select_from(Logs)
        .join(MembersLogs, MembersLogs.log_id == Logs.id)
        .where(Logs.event_id.in_(event_ids))
        .group_by(Logs.event_id)
    )
    for row in session.execute(att_stmt).all():
        event = next((e for e in events if e.id == row.event_id), None)
        if event:
            event.attendance_count = row.attendance_count

    return events


def get_actions_by_event_id(session: Session, event_id: int):
    stmt = (
        select(
            Actions.id.label("action_id"),
            Actions.ar_action_name,
            Departments.ar_name.label("department_ar_name"),
            Departments.id.label("department_id"),
        )
        .select_from(Events)
        .join(Logs, Events.id == Logs.event_id)
        .join(Actions, Logs.action_id == Actions.id)
        .outerjoin(DepartmentsLogs, Logs.id == DepartmentsLogs.log_id)
        .outerjoin(Departments, DepartmentsLogs.department_id == Departments.id)
        .where(Events.id == event_id)
        .group_by(Actions.id, Actions.ar_action_name, Departments.ar_name, Departments.id, Events.id)
        .order_by(Actions.id.asc())
    )
    row = session.execute(stmt).all()
    if not row:
        return None
    return [dict(row._mapping) for row in row]


def get_event_department_ids(session: Session, event_id: int) -> set[int]:
    """The departments an event belongs to: those on its department logs. Usually one."""
    return set(
        session.scalars(
            select(DepartmentsLogs.department_id)
            .join(Logs, Logs.id == DepartmentsLogs.log_id)
            .where(Logs.event_id == event_id)
            .distinct()
        ).all()
    )


def get_event_by_id(session: Session, event_id: int):
    """The plain event row, for the ~15 internal callers that only read its
    fields - no content-link query. Serializing payloads attach them at the
    route layer (see attach_published_content_links_of_event)."""
    statement = select(Events).where(Events.id == event_id)
    event = session.scalars(statement).first()
    if not event:
        raise EventNotFound(event_id)
    return event


# ---------- event content links ----------


def attach_published_content_links_of_all_events(session: Session, events: list[Events]):
    """``GET /events`` without a filter returns every event row, so every
    published link belongs to one of them - the links are selected straight
    off the table, no event-id list shipped with the query."""
    return _attach_published_content_links(
        session,
        events,
        select(EventContentLinks).where(EventContentLinks.published == True),  # noqa: E712
    )


def attach_published_content_links_of_semester(session: Session, events: list[Events], semester_id: str):
    """``GET /events?semester=…`` returns one semester's events; joining the
    links through their event keeps the query to a WHERE instead of an id
    list that grows with the table."""
    stmt = (
        select(EventContentLinks)
        .join(Events, Events.id == EventContentLinks.event_id)
        .where(Events.semester_id == semester_id, EventContentLinks.published == True)  # noqa: E712
        .order_by(EventContentLinks.created_at, EventContentLinks.id)
    )
    return _attach_published_content_links(session, events, stmt)


def attach_published_content_links_of_event(session: Session, event: Events):
    """One event, e.g. ``GET /events/{id}``: a plain WHERE on its own id."""
    stmt = (
        select(EventContentLinks)
        .where(EventContentLinks.event_id == event.id, EventContentLinks.published == True)  # noqa: E712
        .order_by(EventContentLinks.created_at, EventContentLinks.id)
    )
    return _attach_published_content_links(session, [event], stmt)


def _attach_published_content_links(session: Session, events: list[Events], stmt: Select):
    """Set each event's ``content_links`` - its published rows only - in one
    grouped query for the whole batch (one extra SELECT per response, never
    one per event). The full set (drafts included) is an admin concern and is
    read via get_event_content_links, never attached onto an event that a
    serializer could pick up.

    Only call this right before serializing: the public server-side filter is
    what keeps drafts away from the leaderboard app, and every call site is
    payload-building code. The statement passed in must select exactly the
    published rows of the batch's events - that is what the three wrappers
    above build.
    """
    if not events:
        return
    links_by_event: dict[int, list[EventContentLinks]] = {}
    stmt = stmt.order_by(EventContentLinks.created_at, EventContentLinks.id)
    links = session.scalars(stmt).all()
    for link in links:
        set_download_url(link)
        links_by_event.setdefault(link.event_id, []).append(link)
    for event in events:
        if event.id is not None:
            event.content_links = links_by_event.get(event.id, [])


# Google's public direct-download endpoint: the URL the Drive web app itself
# produces for "download anyway", live-verified against public files (200,
# application/octet-stream). The Drive API's documented equivalent
# (files.get?alt=media) needs an access token, so it is not usable by a public
# web page - this one is, for links that are public by design.
_DRIVE_DOWNLOAD_URL = "https://drive.usercontent.google.com/download?id={file_id}&export=download"

_DRIVE_FILE_ID_RE = r"[A-Za-z0-9_-]+"


def _drive_file_id_from_url(url: str) -> str | None:
    """The Drive file id a public share link points at, or None.

    Recognizes only the shapes that carry a plain file: ``drive.google.com
    .com/file/d/<ID>`` and the ``uc?id=<ID>`` export form. Google-hosted
    editors (docs/document/presentation/...) open a viewer, not a file blob,
    so they stay Open-only. Never raises: a malformed URL is simply not a
    download - the modal keeps an Open action and nothing breaks."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        path = parts.path or ""
        if host == "drive.google.com":
            if match := re.match(rf"^/file/d/({_DRIVE_FILE_ID_RE})", path):
                return match.group(1)
        if host in ("drive.google.com", "docs.google.com") and path.rstrip("/") == "/uc":
            file_id = parse_qs(parts.query).get("id", [""])[0]
            if re.fullmatch(_DRIVE_FILE_ID_RE, file_id):
                return file_id
        return None
    except Exception:
        return None


def drive_download_url(link: EventContentLinks) -> str | None:
    """The public direct-download URL sent to the website as ``download_url``,
    or None when there is not one. ``drive_file_id`` (Phase 2's upload flow)
    wins over URL parsing when both exist; a link that is not a Drive file
    (YouTube, other hosts, a malformed URL) yields None.

    Recordings never carry one, even when their URL is a Drive file: nobody
    watches (or wants) an unseen video downloaded to their device - recordings
    stay Open-only."""
    try:
        if link.kind == EventsContentLinkKind.RECORDING:
            return None
        file_id = link.drive_file_id or _drive_file_id_from_url(link.url)
        if not file_id:
            return None
        return _DRIVE_DOWNLOAD_URL.format(file_id=file_id)
    except Exception:
        return None


def set_download_url(link: EventContentLinks) -> None:
    """Read-time derivation: the transient ``download_url`` rides the row into
    its serialization, never into the database."""
    link.download_url = drive_download_url(link)


def get_event_content_links(session: Session, event_id: int, published_only: bool) -> list[EventContentLinks]:
    stmt = (
        select(EventContentLinks)
        .where(EventContentLinks.event_id == event_id)
        .order_by(EventContentLinks.created_at, EventContentLinks.id)
    )
    if published_only:
        stmt = stmt.where(EventContentLinks.published == True)  # noqa: E712
    links = list(session.scalars(stmt).all())
    for link in links:
        set_download_url(link)
    return links


def create_event_content_link(
    session: Session, event_id: int, link_data: "CreateEventContentLink_model", created_by: int | None = None
) -> EventContentLinks:
    link = EventContentLinks(
        event_id=event_id,
        label=link_data.label,
        url=link_data.url,
        kind=link_data.kind,
        published=link_data.published,
        created_by=created_by,
    )
    session.add(link)
    session.flush()
    return link


def get_content_link(session: Session, event_id: int, link_id: str) -> EventContentLinks | None:
    """Scoping by event_id keeps a link id from another event out."""
    return session.scalar(
        select(EventContentLinks).where(EventContentLinks.id == link_id, EventContentLinks.event_id == event_id)
    )


def is_content_link_label_taken(session: Session, event_id: int, label: str, exclude_id: str | None = None) -> bool:
    """A label names the link everywhere it is shown, so an event cannot hold
    two rows with the same name. Compared the way the database compares it -
    a plain ``=`` against the utf8mb4_0900_ai_ci column, case- and
    accent-insensitive - so the friendly pre-check and the unique index can
    never disagree. Values are trimmed by the validators before they get here.
    ``exclude_id`` keeps a row's own label from blocking its own edit."""
    stmt = (
        select(func.count())
        .select_from(EventContentLinks)
        .where(EventContentLinks.event_id == event_id, EventContentLinks.label == label.strip())
    )
    if exclude_id is not None:
        stmt = stmt.where(EventContentLinks.id != exclude_id)
    return bool(session.scalar(stmt))


def update_event_content_link(session: Session, existing: EventContentLinks, link_data: "UpdateEventContentLink_model"):
    """Apply the edit to the row the handler already fetched (404 handled
    there), applying only the present fields - a row is never cleared."""
    if link_data.label is not None:
        existing.label = link_data.label
    if link_data.url is not None:
        existing.url = link_data.url
    if link_data.kind is not None:
        existing.kind = link_data.kind
    if link_data.published is not None:
        existing.published = link_data.published
    session.flush()
    return existing


def delete_event_content_link(session: Session, event_id: int, link_id: str) -> EventContentLinks | None:
    link = get_content_link(session, event_id, link_id)
    if not link:
        return None
    session.delete(link)
    session.flush()
    return link


def create_event(session: Session, event_data: Events_model, responsible_member_id: int, created_by: int):
    new_event = Events(
        name=event_data.name,
        location_type=event_data.location_type,
        location=event_data.location,
        start_datetime=event_data.start_datetime,
        end_datetime=event_data.end_datetime,
        description=event_data.description,
        status=event_data.status,
        is_official=event_data.is_official,
        image_url=event_data.image_url,
        meeting_url=event_data.meeting_url,
        semester_id=semester_for_event(session, event_data.end_datetime, event_data.semester_id).id,
        created_at=datetime.now(),
        responsible_member_id=responsible_member_id,
        created_by=created_by,
    )
    session.add(new_event)
    session.flush()
    return new_event


def delete_event(session: Session, event_id: int):
    event = session.scalar(select(Events).where(Events.id == event_id))
    if not event:
        return None
    session.delete(event)
    session.flush()
    return event


def update_event(session: Session, event_id: int, event_data: Events_model):
    existing_event = session.scalar(select(Events).where(Events.id == event_id))
    if not existing_event:
        return None
    try:
        # An explicit semester wins. Otherwise a new end date re-derives it, and
        # an unchanged one keeps whatever the event is already filed under.
        if event_data.semester_id is not None or event_data.end_datetime.date() != existing_event.end_datetime.date():
            existing_event.semester_id = semester_for_event(session, event_data.end_datetime, event_data.semester_id).id
        existing_event.name = event_data.name
        existing_event.location_type = event_data.location_type
        existing_event.location = event_data.location
        existing_event.start_datetime = event_data.start_datetime
        existing_event.end_datetime = event_data.end_datetime
        existing_event.description = event_data.description
        existing_event.status = EventsStatus(event_data.status)
        existing_event.image_url = event_data.image_url
        # meeting_url is owned by update_event_meeting_url, and the edit form does not
        # carry it, so it otherwise survives untouched here. Except: once the event stops
        # being online, a leftover link must not silently reappear if it goes online again.
        if event_data.location_type != EventsLocationType.ONLINE:
            existing_event.meeting_url = None
        session.flush()
        return existing_event
    except IntegrityError as e:
        session.rollback()
        logger.warning("IntegrityError in update_event: %s", e)
        return -1


def update_event_meeting_url(session: Session, event_id: int, meeting_url: str | None):
    event = session.scalar(select(Events).where(Events.id == event_id))
    if not event:
        return None
    event.meeting_url = meeting_url
    session.flush()
    return event


def get_member_events(session: Session, member_id: int):
    stmt = (
        select(Events, MembersLogs.date)
        .join(Logs, Events.id == Logs.event_id)
        .join(MembersLogs, MembersLogs.log_id == Logs.id)
        .where(
            MembersLogs.member_id == member_id,
            Events.status != EventsStatus.DRAFT,
            Events.location_type != EventsLocationType.HIDDEN,
        )
        .order_by(Events.start_datetime.desc(), MembersLogs.date)
    )
    results = session.execute(stmt).all()

    events_map: dict[int, dict] = {}
    for event, att_date in results:
        if event.id not in events_map:
            events_map[event.id] = {"event": event, "dates": []}
        events_map[event.id]["dates"].append(att_date)

    threshold = config.ATTENDANCE_EARLY_HOURS_THRESHOLD
    attended = []
    participated = []
    for data in events_map.values():
        event = data["event"]
        raw_dates = data["dates"]
        event_dict = {col.name: getattr(event, col.name) for col in event.__table__.columns}

        if event.location_type == EventsLocationType.NONE:
            participated.append(event_dict)
        else:
            total_days = max((event.end_datetime - event.start_datetime).days + 1, 1)
            attended_set = {get_effective_date(d, threshold) for d in raw_dates}
            attendance_dates = []
            for day_offset in range(total_days):
                day = event.start_datetime.date() + timedelta(days=day_offset)
                attendance_dates.append(
                    {"date": datetime.combine(day, datetime.min.time()), "attended": day in attended_set}
                )
            event_dict["attendance_dates"] = attendance_dates
            attended.append(event_dict)

    return attended, participated
