"""The caller's ``Access`` as a FastAPI dependency.

FastAPI caches a dependency for the length of one request, so every guard and
handler that asks for ``CurrentAccess`` shares one resolve.
"""

from typing import Annotated

from fastapi import Depends

from app.dependencies import DB
from app.helpers import MemberOrGuest
from app.services.permissions.access import Access, resolve_access


def get_access(session: DB, member: MemberOrGuest) -> Access:
    return resolve_access(session, member)


CurrentAccess = Annotated[Access, Depends(get_access)]
