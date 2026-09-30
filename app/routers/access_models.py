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
