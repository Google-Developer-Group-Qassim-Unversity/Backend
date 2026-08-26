"""Turn a request's event/member reference into the shape the email APIs want.

Shared by the routes and by the background jobs, which is why these live here
rather than in either.
"""

from app.DB import events as events_queries
from app.DB import members as members_queries
from app.routers.email_models import ManualCertificateMember, ManualCertificateRequest, SimpleEvent, SimpleMember
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
        return (SimpleMember(name=member.name, email=member.email, gender=member.gender), member_item.member_id)
    assert member_item.member is not None
    return member_item.member, None
