"""Read-only inventory of the rows reached by an event's database cascades.

Use the schema's foreign keys so a newly attached resource cannot silently go
missing from the review. Row inspection exposes only a small display allowlist.
"""

from enum import Enum
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, func, or_, select
from sqlalchemy.sql.elements import Label
from sqlalchemy.orm import Session

from app.config import config
from app.DB.schema import Base, Events, Members, Departments, PushNotifications, PushKind, PushStatus


class DeletionResource(BaseModel):
    key: str
    count: int


class EventDeletionImpact(BaseModel):
    event_id: int
    event_name: str
    resources: list[DeletionResource]
    total: int
    retained_notifications: int


class DeletionItem(BaseModel):
    id: str
    label: str
    details: dict[str, str]


class DeletionResourcePage(BaseModel):
    items: list[DeletionItem]
    total: int
    page: int
    page_size: int


def _kept_notifications(event_id: int):
    return (
        select(func.max(PushNotifications.id))
        .where(
            PushNotifications.event_id == event_id,
            PushNotifications.kind == PushKind.EVENT_CANCELLED,
            PushNotifications.status == PushStatus.PENDING,
        )
        .group_by(PushNotifications.member_id)
    )


def resource_filters(event_id: int):
    """One predicate per cascaded table, including transitive dependencies."""
    filters = {"events": Events.__table__.c.id == event_id}
    for table in Base.metadata.sorted_tables:
        if table.name == "events":
            continue
        paths = []
        for fk in table.foreign_key_constraints:
            parent = fk.referred_table
            if fk.ondelete == "CASCADE" and parent.name in filters:
                # All event descendants currently use single-column cascade keys.
                element = next(iter(fk.elements))
                paths.append(element.parent.in_(select(element.column).where(filters[parent.name])))
        if paths:
            filters[table.name] = or_(*paths)
    if config.push_enabled and "push_notifications" in filters:
        filters["push_notifications"] &= PushNotifications.id.not_in(_kept_notifications(event_id))
    return filters


def impact(session: Session, event: Events) -> EventDeletionImpact:
    resources = [
        DeletionResource(
            key=key, count=session.scalar(select(func.count()).select_from(Base.metadata.tables[key]).where(where)) or 0
        )
        for key, where in resource_filters(event.id).items()
        if key != "events"
    ]
    retained = 0
    if config.push_enabled:
        retained = session.scalar(select(func.count()).select_from(_kept_notifications(event.id).subquery())) or 0
    return EventDeletionImpact(
        event_id=event.id,
        event_name=event.name,
        resources=resources,
        total=1 + sum(row.count for row in resources),
        retained_notifications=retained,
    )


def resource_page(session: Session, event_id: int, resource: str, page: int, page_size: int) -> DeletionResourcePage:
    filters = resource_filters(event_id)
    if resource == "events" or resource not in filters:
        raise HTTPException(404, "Deletion resource not found")
    table = Base.metadata.tables[resource]
    where = filters[resource]
    total = session.scalar(select(func.count()).select_from(table).where(where)) or 0
    columns: list[Column[Any] | Label[Any]] = list(table.primary_key.columns)
    # Never expose payloads, answers, tokens, email contents or integration errors.
    for name in (
        "name",
        "label",
        "status",
        "team",
        "kind",
        "form_type",
        "submission_type",
        "is_accepted",
        "is_invited",
        "date",
        "submitted_at",
        "cancelled_at",
        "type",
        "value",
        "points",
    ):
        if name in table.c and name not in {column.name for column in columns}:
            columns.append(table.c[name])
    for field, model in (("member_id", Members), ("department_id", Departments)):
        if field in table.c:
            columns.append(select(model.name).where(model.id == table.c[field]).scalar_subquery().label("display_name"))
            break
    rows = session.execute(
        select(*columns)
        .where(where)
        .order_by(*table.primary_key.columns)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).mappings()
    items = []
    for row in rows:
        identifier = " / ".join(str(row[c.name]) for c in table.primary_key.columns)
        label = row.get("display_name") or row.get("label") or row.get("name") or f"#{identifier}"
        details = {}
        for column in columns:
            key = column.name
            if key in {"display_name", "name", "label"} or key in table.primary_key.columns or row[key] is None:
                continue
            value = row[key]
            details[key] = str(value.value if isinstance(value, Enum) else value)
        items.append(DeletionItem(id=identifier, label=label, details=details))
    return DeletionResourcePage(items=items, total=total, page=page, page_size=page_size)
