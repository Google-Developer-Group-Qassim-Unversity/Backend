"""Route guards on top of ``Access``.

    @router.put("/{event_id:int}", dependencies=[Depends(Require(Perm.EVENTS_EDIT, event_departments))])

- ``Staff``: on the current roster, or a super admin. The admin app's baseline.
- ``Require(perm)``: the caller has ``perm`` (for a ``dept`` permission, in any department).
- ``Require(perm, resolver)``: the caller has ``perm`` for the department(s) the
  resolver finds from the request, e.g. the event in the path. See ``departments.py``.
- ``SuperAdmin``.

Every guard also runs ``authenticated_guard``, so a caller with no token is
refused by Clerk before anything is resolved.

Each guard carries an ``auth_label`` that ``tests/test_route_auth.py`` pins per route.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends

from app.exceptions import SuperAdminRequired
from app.helpers import authenticated_guard
from app.services.permissions.access import Access
from app.services.permissions.catalogue import Perm
from app.services.permissions.dependencies import CurrentAccess

DepartmentResolver = Callable[..., frozenset[int]]


def _no_departments() -> frozenset[int]:
    return frozenset()


def Require(perm: Perm, departments: DepartmentResolver | None = None) -> Callable[..., Access]:
    def anywhere(access: CurrentAccess, _credentials: Any = Depends(authenticated_guard)) -> Access:
        access.require(perm)
        return access

    def for_departments(
        access: CurrentAccess,
        department_ids: Annotated[frozenset[int], Depends(departments or _no_departments)],
        _credentials: Any = Depends(authenticated_guard),
    ) -> Access:
        access.require_any(perm, department_ids)
        return access

    if departments is None:
        guard, label = anywhere, perm.value
    else:
        guard, label = for_departments, f"{perm.value} for {getattr(departments, 'label', departments.__name__)}"

    guard.__name__ = f"require_{perm.name.lower()}"
    setattr(guard, "auth_label", label)
    return guard


Staff = Require(Perm.ADMIN_ACCESS)


def SuperAdmin(access: CurrentAccess, _credentials: Any = Depends(authenticated_guard)) -> Access:
    if not access.is_super_admin:
        raise SuperAdminRequired()
    return access


setattr(SuperAdmin, "auth_label", "super_admin")
