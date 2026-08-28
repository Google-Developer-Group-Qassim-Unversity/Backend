"""Turn a request's event/member reference into the shape the email APIs want.

Shared by the routes and by the background jobs, which is why these live here
rather than in either.
"""

from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.DB import events as events_queries
from app.DB import members as members_queries
from app.routers.email_models import (
    BlastGuaranteedRecipient,
    ManualCertificateMember,
    ManualCertificateRequest,
    SimpleEvent,
    SimpleMember,
)
from app.services.email_capacity import format_event_date


def _resolve_event(request: ManualCertificateRequest, session) -> tuple[SimpleEvent, int | None]:
    if request.event_id:
        event = events_queries.get_event_by_id(session, request.event_id)
        return (
            SimpleEvent(name=event.name, date=format_event_date(event), official=bool(event.is_official)),
            request.event_id,
        )
    assert request.event is not None
    return request.event, None


def _resolve_member(member_item: ManualCertificateMember, session) -> tuple[SimpleMember, int | None]:
    if member_item.member_id:
        member = members_queries.get_member_by_id(session, member_item.member_id)
        if not member.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Member [{member_item.member_id}] has no email on file"
            )
        return (SimpleMember(name=member.name, email=member.email, gender=member.gender), member_item.member_id)
    assert member_item.member is not None
    return member_item.member, None


def resolve_ad_hoc_recipients(
    session: Session, items: Sequence[BlastGuaranteedRecipient], *, include_member_id: bool
) -> tuple[list[dict], set[int]]:
    """Resolve a member_id-or-email recipient list into deduped {name, email} dicts.

    Shared by the direct-email and blast routes, which each built this the
    same way: look up any member_ids in one query, then merge in the
    manually-typed addresses, de-duplicating by lowercased email so a member
    picked twice - or picked by both member_id and a manually-typed matching
    address - collapses to one entry.

    `include_member_id` controls whether each dict carries a `member_id` key.
    Direct email needs it (for the email log's `member_id` column); blast
    doesn't thread it through its recipient dicts today, so leaving it out
    keeps guaranteed recipients shaped the same as the algorithmically
    selected ones they get merged with.

    Returns the recipients alongside the set of member_ids that were actually
    resolved, so a caller that also draws from a member pool (blast) can
    exclude them without re-deriving the lookup itself.
    """
    member_ids = [item.member_id for item in items if item.member_id is not None]
    members_by_id = {m.id: m for m in members_queries.get_members_by_id(session, member_ids)} if member_ids else {}

    resolved: dict[str, dict] = {}
    for item in items:
        if item.member_id is not None:
            member = members_by_id.get(item.member_id)
            if member is None or not member.email:
                continue
            entry: dict[str, str | int | None] = {"name": member.name, "email": member.email}
            if include_member_id:
                entry["member_id"] = member.id
            resolved[member.email.lower()] = entry
        elif item.email is not None:
            entry = {"name": item.name, "email": item.email}
            if include_member_id:
                entry["member_id"] = None
            resolved[item.email.lower()] = entry

    return list(resolved.values()), set(members_by_id.keys())
