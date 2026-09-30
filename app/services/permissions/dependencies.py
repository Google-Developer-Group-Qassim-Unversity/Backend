"""The caller's ``Access`` as a FastAPI dependency.

FastAPI caches a dependency for the length of one request, so every guard and
handler that asks for ``CurrentAccess`` shares one resolve.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.dependencies import DB
from app.DB.schema import Members
from app.helpers import CurrentMember, MemberOrGuest
from app.services.permissions.access import Access, resolve_access


def get_access(session: DB, member: MemberOrGuest) -> Access:
    return resolve_access(session, member)


CurrentAccess = Annotated[Access, Depends(get_access)]


@dataclass(frozen=True)
class Caller:
    """A signed-in member and what they can do, for services that need both (e.g. to record who acted)."""

    member: Members
    access: Access


def get_caller(member: CurrentMember, access: CurrentAccess) -> Caller:
    return Caller(member=member, access=access)


CurrentCaller = Annotated[Caller, Depends(get_caller)]
