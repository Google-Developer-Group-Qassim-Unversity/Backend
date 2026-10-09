from pydantic import BaseModel


class AccessSemester(BaseModel):
    id: str
    name: str
    hijri_code: int


class AccessDepartment(BaseModel):
    """A department the caller is on this semester's roster of."""

    id: int
    name: str
    ar_name: str
    roles: list[str]
    # The department-scoped permissions the caller has for this department.
    permissions: list[str]


class AccessMe(BaseModel):
    member_id: int | None
    is_staff: bool
    is_super_admin: bool
    semester: AccessSemester | None
    departments: list[AccessDepartment]
    # Every permission the caller has somewhere. A super admin has them all.
    permissions: list[str]


class AccessForEvent(BaseModel):
    event_id: int
    # The permissions the caller has for this event: department-scoped ones checked against its department(s).
    permissions: list[str]


class StagingSignInCheck(BaseModel):
    email: str


class StagingSignInAnswer(BaseModel):
    # Whether this email belongs to a current staff member (or a super admin).
    is_staff: bool
