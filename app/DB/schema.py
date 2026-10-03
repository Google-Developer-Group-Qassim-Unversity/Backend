from typing import Optional
import datetime
import enum
import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Time,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.mysql import CHAR, DATETIME, INTEGER, LONGTEXT, SMALLINT, TEXT, TINYINT, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# IMPORTANT: For any relationship where the FK has ON DELETE CASCADE in the database,
# add passive_deletes=True to let the database handle cascades. Without this,
# SQLAlchemy may try to NULL out FK columns before delete, causing IntegrityErrors.

"""
============= Data Model Breakdown =============
this DB has 2 main functions
1. Track points for departments and members
2. allow members to sign up for events and allow department admins to recive submissions and manage acceptance for their events
# How points work:
    pretty simple actually, the main table is members_logs/departments_logs which link members/departments to a 'log'
    a 'log' represents an 'action' taken on an events actions have a point value
    for example: if we have an action for 'attended course' worth 6 points, the way we give a member points for this action is by creating a member_log connecting the member_id and a log
    this log is linked to the 'attended course' action and to the event 'course 1'
    this way many member can get points for the same action and event.
    a similar process happens for departments 
# How events and submissions work:
    we have an events table that has all the details about the event.
    the main way members can sign up for an event is using a form
    a form can be of multiple types (none, registration, google)
        - 'none' means this event requires no signup and is open for all members to attend (in the UI there is no singup button)
        - 'registration' means that member need to 'signup' for the event (in the UI there is a signup button, on_click this creates a 'submission' with type 'registration' and is_accepted = 0, then the department admin can accept or reject this submission)
        - 'google' means that the members need to fill a google form (in the UI after users click signup their submission is set to 'partial' and then they get redirected to a google form, once they fill it google sends a POST to our webhook endpoint with the submission details and then we update the submission to 'google')
    a submission represents a member's attempt to sign up for an event, it has a type (registration, partial, google)
    a submission is created when a member clicks the signup button for an event, the submission type is based on the form type
"""


class ActionsActionType(str, enum.Enum):
    COMPOSITE = "composite"
    DEPARTMENT = "department"
    MEMBER = "member"
    BONUS = "bonus"


class DepartmentsType(str, enum.Enum):
    ADMINISTRATIVE = "administrative"
    PRACTICAL = "practical"


class EventsLocationType(str, enum.Enum):
    ONLINE = "online"
    ON_SITE = "on-site"
    NONE = "none"
    HIDDEN = "hidden"


class EventsStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    ACTIVE = "active"
    CLOSED = "closed"


class FormType(str, enum.Enum):
    NONE = "none"
    REGISTRATION = "registration"
    GOOGLE = "google"


class MembersGender(str, enum.Enum):
    MALE = "Male"
    FEMALE = "Female"


class ModificationsType(str, enum.Enum):
    BONUS = "bonus"
    DISCOUNT = "discount"


class MemberProfilesNameLanguage(str, enum.Enum):
    AR = "ar"
    EN = "en"


class SubmissionsSubmissionType(str, enum.Enum):
    NONE = "none"
    REGISTRATION = "registration"
    PARTIAL = "partial"
    GOOGLE = "google"


class EmailJobsStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class EmailJobsType(str, enum.Enum):
    EVENT_CERTIFICATE = "event-certificate"
    MANUAL_CERTIFICATE = "manual-certificate"
    CUSTOM_EMAIL = "custom-email"
    DIRECT_EMAIL = "direct-email"
    BLAST = "blast"
    ACCEPTANCE = "acceptance"


class FormsSubmissionsFormType(str, enum.Enum):
    NONE = "none"
    REGISTRATION = "registration"
    GOOGLE = "google"


class FormSyncJobsStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class FormsSubmissionsSubmissionType(str, enum.Enum):
    NONE = "none"
    REGISTRATION = "registration"
    PARTIAL = "partial"
    GOOGLE = "google"


class FormsSubmissionsGender(str, enum.Enum):
    MALE = "Male"
    FEMALE = "Female"


class OpenEventsFormType(str, enum.Enum):
    NONE = "none"
    REGISTRATION = "registration"
    GOOGLE = "google"


class OpenEventsLocationType(str, enum.Enum):
    ONLINE = "online"
    ON_SITE = "on-site"
    NONE = "none"
    HIDDEN = "hidden"


class EmailLogsFromAddress(str, enum.Enum):
    INFO_KERNELTICS = "info@kerneltics.com"
    GDG_QASSIM = "gdg.qu1@gmail.com"


class EmailProvider(str, enum.Enum):
    GOOGLE = "google"
    SES = "ses"


class EmailLogsEmailType(str, enum.Enum):
    EVENT_CERTIFICATE = "event-certificate"
    MANUAL_CERTIFICATE = "manual-certificate"
    EVENT_ANNOUNCEMENT = "event_announcement"
    ACCEPTANCE = "acceptance"
    BLAST = "blast"
    DIRECT = "direct"


class OpenEventsStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    ACTIVE = "active"
    CLOSED = "closed"


class SemesterTerm(str, enum.Enum):
    FIRST = "first"
    SECOND = "second"
    SUMMER = "summer"


# Every UUID column shares one charset/collation: MySQL foreign keys need both
# sides to match, and tables here do not share a default charset.
UUID_CHAR = CHAR(36, charset="ascii", collation="ascii_bin")


class Actions(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    action_name: Mapped[str] = mapped_column(
        VARCHAR(60, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
    )
    points: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    action_type: Mapped[ActionsActionType] = mapped_column(
        Enum(ActionsActionType, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    ar_action_name: Mapped[str] = mapped_column(
        VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("'99'"))
    is_hidden: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'0'"))

    logs: Mapped[list["Logs"]] = relationship("Logs", back_populates="action", passive_deletes=True)


class Departments(Base):
    __tablename__ = "departments"
    __table_args__ = (Index("uq_departments_club_leadership", "club_leadership_key", unique=True),)

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[DepartmentsType] = mapped_column(
        Enum(DepartmentsType, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    ar_name: Mapped[str] = mapped_column(
        VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
    )
    active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'1'"))
    color: Mapped[str] = mapped_column(String(7), nullable=False, server_default=text("'#4285f4'"))
    icon: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'users'"))
    # Left out of the department ranking when 0 (the Board, Leadership).
    show_in_leaderboard: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'1'"))
    # The department whose leaders are the club's presidents. At most one:
    # the generated key is NULL on every other row, and it is unique.
    is_club_leadership: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'0'"))
    club_leadership_key: Mapped[Optional[int]] = mapped_column(
        TINYINT(unsigned=True), Computed("CASE WHEN is_club_leadership = 1 THEN 1 END", persisted=True)
    )
    # The creation date of departments that predate this feature is unknown.
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime, nullable=True, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")
    )

    departments_logs: Mapped[list["DepartmentsLogs"]] = relationship(
        "DepartmentsLogs", back_populates="department", passive_deletes=True
    )


class ClubRoles(Base):
    """A role someone can hold in a department for a semester: member, vp or leader.

    Rows, not an enum, so a department-specific role later is a new row.
    ``max_holders`` is the default seat limit per department per semester
    (NULL = unlimited); ``DepartmentRoleLimits`` overrides it for one department.
    """

    __tablename__ = "club_roles"
    __table_args__ = (Index("uq_club_roles_key", "key", unique=True),)

    id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ar_name: Mapped[str] = mapped_column(String(100), nullable=False)
    max_holders: Mapped[Optional[int]] = mapped_column(TINYINT(unsigned=True))
    sort_order: Mapped[int] = mapped_column(TINYINT(unsigned=True), nullable=False)


class DepartmentRoleLimits(Base):
    """A department's own seat limit for a role, e.g. Leadership allows two leaders."""

    __tablename__ = "department_role_limits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_department_role_limits_department", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["role_id"], ["club_roles.id"], name="fk_department_role_limits_role", ondelete="CASCADE"),
    )

    department_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    role_id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True)
    max_holders: Mapped[Optional[int]] = mapped_column(TINYINT(unsigned=True))


class SemesterDepartments(Base):
    """A department that existed in a semester, with the name it had then (NULL = its current name)."""

    __tablename__ = "semester_departments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["semester_id"], ["semesters.id"], name="fk_semester_departments_semester", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_semester_departments_department", ondelete="RESTRICT"
        ),
    )

    semester_id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True)
    department_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(50))
    ar_name: Mapped[Optional[str]] = mapped_column(VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))

    department: Mapped["Departments"] = relationship("Departments")


class ClubMemberships(Base):
    """One role one person holds in one department for one semester.

    A leader or VP also has a ``member`` row in the same department; the
    service keeps the two in step. Seat limits are checked by the service
    under a lock on the ``semester_departments`` row.
    """

    __tablename__ = "club_memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["semester_id", "department_id"],
            ["semester_departments.semester_id", "semester_departments.department_id"],
            name="fk_club_memberships_semester_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_club_memberships_member", ondelete="RESTRICT"),
        ForeignKeyConstraint(["role_id"], ["club_roles.id"], name="fk_club_memberships_role", ondelete="RESTRICT"),
        Index("uq_club_memberships_member_role", "semester_id", "department_id", "member_id", "role_id", unique=True),
        Index("ix_club_memberships_member", "member_id", "semester_id"),
        Index("ix_club_memberships_role", "semester_id", "department_id", "role_id"),
    )

    id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True, default=lambda: str(uuid.uuid4()))
    semester_id: Mapped[str] = mapped_column(UUID_CHAR, nullable=False)
    department_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    member_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    role_id: Mapped[str] = mapped_column(UUID_CHAR, nullable=False)
    # Clerk subject IDs are available even when an admin has no members row.
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )

    member: Mapped["Members"] = relationship("Members")
    role: Mapped["ClubRoles"] = relationship("ClubRoles")


class ClubMembershipAction(str, enum.Enum):
    ADDED = "added"
    REMOVED = "removed"


class ClubMembershipChanges(Base):
    """Append-only: who added or removed which role, when."""

    __tablename__ = "club_membership_changes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["semester_id"], ["semesters.id"], name="fk_club_membership_changes_semester", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_club_membership_changes_department", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_club_membership_changes_member", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["role_id"], ["club_roles.id"], name="fk_club_membership_changes_role", ondelete="RESTRICT"
        ),
        Index("ix_club_membership_changes_scope", "semester_id", "department_id", "created_at"),
        Index("ix_club_membership_changes_member", "member_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True, default=lambda: str(uuid.uuid4()))
    semester_id: Mapped[str] = mapped_column(UUID_CHAR, nullable=False)
    department_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    member_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    role_id: Mapped[str] = mapped_column(UUID_CHAR, nullable=False)
    action: Mapped[ClubMembershipAction] = mapped_column(
        Enum(ClubMembershipAction, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=text("CURRENT_TIMESTAMP(6)")
    )

    member: Mapped["Members"] = relationship("Members")
    role: Mapped["ClubRoles"] = relationship("ClubRoles")
    department: Mapped["Departments"] = relationship("Departments")


class Events(Base):
    __tablename__ = "events"
    __table_args__ = (
        ForeignKeyConstraint(["semester_id"], ["semesters.id"], ondelete="RESTRICT", name="fk_events_semester"),
        Index("event_name", "name"),
        Index("events_id_IDX", "id", "name"),
        Index("ix_events_semester_start", "semester_id", "start_datetime"),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    name: Mapped[str] = mapped_column(VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False)
    location_type: Mapped[EventsLocationType] = mapped_column(
        Enum(EventsLocationType, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    location: Mapped[str] = mapped_column(
        VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
    )
    start_datetime: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    end_datetime: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    status: Mapped[EventsStatus] = mapped_column(
        Enum(EventsStatus, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    description: Mapped[Optional[str]] = mapped_column(TEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    image_url: Mapped[Optional[str]] = mapped_column(VARCHAR(500, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    # Join link for remote events. Owned by PUT /events/{id}/meeting-url, not by the
    # full-event update, so editing an event cannot silently drop it.
    meeting_url: Mapped[Optional[str]] = mapped_column(VARCHAR(500, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    is_official: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))
    # The semester the event counts toward. Set from the end date when the event
    # is saved (app/semesters.py), and never moved by editing a semester's dates.
    semester_id: Mapped[str] = mapped_column(UUID_CHAR, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    semester: Mapped["Semesters"] = relationship("Semesters")
    forms: Mapped[list["Forms"]] = relationship("Forms", back_populates="event", passive_deletes=True)
    logs: Mapped[list["Logs"]] = relationship("Logs", back_populates="event", passive_deletes=True)
    email_logs: Mapped[list["EmailLogs"]] = relationship("EmailLogs", back_populates="event", passive_deletes=True)


class Members(Base):
    __tablename__ = "members"
    __table_args__ = (
        Index("uni_id", "uni_id", unique=True),
        Index("ix_members_clerk_user_id", "clerk_user_id", unique=True),
        Index("ix_members_email", "email", unique=True),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    uni_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    clerk_user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    gender: Mapped[MembersGender] = mapped_column(
        Enum(MembersGender, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    uni_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uni_college: Mapped[Optional[str]] = mapped_column(
        VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    is_authenticated: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'0'"))
    email: Mapped[Optional[str]] = mapped_column(String(100))
    phone_number: Mapped[Optional[str]] = mapped_column(String(20))

    profile: Mapped[Optional["MemberProfiles"]] = relationship(
        "MemberProfiles", back_populates="member", uselist=False, passive_deletes=True
    )
    members_logs: Mapped[list["MembersLogs"]] = relationship("MembersLogs", back_populates="member")
    submissions: Mapped[list["Submissions"]] = relationship(
        "Submissions", back_populates="member", passive_deletes=True
    )
    email_logs: Mapped[list["EmailLogs"]] = relationship(
        "EmailLogs", back_populates="member", passive_deletes=True, foreign_keys="EmailLogs.member_id"
    )


class Forms(Base):
    __tablename__ = "forms"
    __table_args__ = (
        ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE", onupdate="CASCADE", name="forms_ibfk_1"),
        Index("forms_unique_event_id", "event_id", unique=True),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    event_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    form_type: Mapped[FormType] = mapped_column(
        Enum(FormType, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    google_form_id: Mapped[Optional[str]] = mapped_column(
        VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
    )
    google_watch_id: Mapped[Optional[str]] = mapped_column(String(100))
    google_responders_url: Mapped[Optional[str]] = mapped_column(
        VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
    )
    admin_google_email: Mapped[Optional[str]] = mapped_column(
        VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
    )

    event: Mapped["Events"] = relationship("Events", back_populates="forms")
    submissions: Mapped[list["Submissions"]] = relationship("Submissions", back_populates="form", passive_deletes=True)
    access_grants: Mapped[list["FormAccessGrants"]] = relationship(
        "FormAccessGrants", back_populates="form", passive_deletes=True
    )

    @property
    def granted_emails(self) -> list[str]:
        """Every Google email actually granted Drive access to this form - not just
        the last one, since admin_google_email only ever remembers the most recent
        grant and silently "forgets" earlier ones when a second admin requests
        access. See docs/GOOGLE_FORMS.md."""
        return [grant.google_email for grant in self.access_grants]


class FormAccessGrants(Base):
    __tablename__ = "form_access_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["form_id"], ["forms.id"], ondelete="CASCADE", onupdate="CASCADE", name="form_access_grants_ibfk_1"
        ),
        Index("form_access_grants_unique_form_email", "form_id", "google_email", unique=True),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    form_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    google_email: Mapped[str] = mapped_column(
        VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
    )
    granted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    form: Mapped["Forms"] = relationship("Forms", back_populates="access_grants")


class Logs(Base):
    __tablename__ = "logs"
    __table_args__ = (
        ForeignKeyConstraint(["action_id"], ["actions.id"], ondelete="CASCADE", onupdate="CASCADE", name="logs_ibfk_1"),
        ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE", name="fk_events"),
        Index("action_id", "action_id"),
        Index("fk_events", "event_id"),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    action_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    event_id: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))

    action: Mapped["Actions"] = relationship("Actions", back_populates="logs")
    event: Mapped[Optional["Events"]] = relationship("Events", back_populates="logs")
    departments_logs: Mapped[list["DepartmentsLogs"]] = relationship(
        "DepartmentsLogs", back_populates="log", passive_deletes=True
    )
    members_logs: Mapped[list["MembersLogs"]] = relationship("MembersLogs", back_populates="log", passive_deletes=True)
    modifications: Mapped[list["Modifications"]] = relationship(
        "Modifications", back_populates="log", passive_deletes=True
    )


class MemberProfiles(Base):
    __tablename__ = "member_profiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["member_id"], ["members.id"], ondelete="CASCADE", onupdate="CASCADE", name="fk_member_profiles_member"
        ),
        Index("idx_member_profiles_member_id", "member_id", unique=True),
        Index("idx_member_profiles_uuid", "uuid", unique=True),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False, unique=True)
    # No index=True here: combined with unique=True SQLAlchemy would emit a single
    # index named ix_member_profiles_uuid, but the DB has a uq_ unique constraint
    # plus the explicit idx_ index declared in __table_args__ above.
    uuid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    custom_name: Mapped[Optional[str]] = mapped_column(VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    theme_id: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'gdg-blue'"))
    name_language: Mapped[MemberProfilesNameLanguage] = mapped_column(
        Enum(MemberProfilesNameLanguage, values_callable=lambda cls: [member.value for member in cls]),
        nullable=False,
        server_default=text("'ar'"),
    )
    user_status: Mapped[Optional[str]] = mapped_column(String(50))
    education_level: Mapped[Optional[str]] = mapped_column(String(50))
    institution: Mapped[Optional[str]] = mapped_column(VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    major: Mapped[Optional[str]] = mapped_column(VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    study_year_or_level: Mapped[Optional[str]] = mapped_column(
        VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
    )
    bio: Mapped[Optional[str]] = mapped_column(TEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    social_links: Mapped[Optional[list]] = mapped_column(JSON)  # a list of {platform, url} objects, not a dict
    visibility: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")
    )

    member: Mapped["Members"] = relationship("Members", back_populates="profile")


class DepartmentsLogs(Base):
    __tablename__ = "departments_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="departments_logs_departments_FK",
        ),
        ForeignKeyConstraint(
            ["log_id"], ["logs.id"], ondelete="CASCADE", onupdate="CASCADE", name="departments_logs_logs_FK"
        ),
        Index("departments_logs_departments_FK", "department_id"),
        Index("departments_logs_idx", "log_id", "department_id"),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    department_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    log_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)

    department: Mapped["Departments"] = relationship("Departments", back_populates="departments_logs")
    log: Mapped["Logs"] = relationship("Logs", back_populates="departments_logs")


class MembersLogs(Base):
    __tablename__ = "members_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["log_id"], ["logs.id"], ondelete="CASCADE", onupdate="CASCADE", name="members_logs_logs_FK"
        ),
        ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_members_id"),
        Index("fk_members_id", "member_id"),
        Index("idx_members_logs_log_id", "log_id"),
        Index("unique_member_log_day", "member_id", "log_id", "date", unique=True),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    member_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    log_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    date: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    log: Mapped["Logs"] = relationship("Logs", back_populates="members_logs")
    member: Mapped["Members"] = relationship("Members", back_populates="members_logs")


class Modifications(Base):
    __tablename__ = "modifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["log_id"], ["logs.id"], ondelete="CASCADE", onupdate="CASCADE", name="modifications_ibfk_1"
        ),
        Index("log_id", "log_id"),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    log_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    type: Mapped[ModificationsType] = mapped_column(
        Enum(ModificationsType, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    value: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)

    log: Mapped["Logs"] = relationship("Logs", back_populates="modifications")


class Submissions(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["form_id"], ["forms.id"], ondelete="CASCADE", onupdate="CASCADE", name="submissions_ibfk_1"
        ),
        ForeignKeyConstraint(
            ["member_id"], ["members.id"], ondelete="CASCADE", onupdate="CASCADE", name="submissions_ibfk_2"
        ),
        Index("from_id_member_id_idx", "form_id", "member_id"),
        Index("submissions_unique", "member_id", "form_id", unique=True),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    form_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    member_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    is_accepted: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'0'"))
    is_invited: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'0'"))
    submitted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    submission_type: Mapped[SubmissionsSubmissionType] = mapped_column(
        Enum(SubmissionsSubmissionType, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    google_submission_id: Mapped[Optional[str]] = mapped_column(String(100))
    google_submission_value: Mapped[Optional[dict]] = mapped_column(JSON)

    form: Mapped["Forms"] = relationship("Forms", back_populates="submissions")
    member: Mapped["Members"] = relationship("Members", back_populates="submissions")


class EmailLogs(Base):
    __tablename__ = "email_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["member_id"], ["members.id"], ondelete="CASCADE", onupdate="CASCADE", name="fk_email_logs_member"
        ),
        ForeignKeyConstraint(
            ["event_id"], ["events.id"], ondelete="CASCADE", onupdate="CASCADE", name="fk_email_logs_event"
        ),
        ForeignKeyConstraint(
            ["sent_by"], ["members.id"], ondelete="CASCADE", onupdate="CASCADE", name="fk_email_logs_sent_by"
        ),
        Index("fk_email_logs_member", "member_id"),
        Index("fk_email_logs_event", "event_id"),
        Index("fk_email_logs_sent_by", "sent_by"),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    member_id: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))
    event_id: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))
    from_address: Mapped[str] = mapped_column(String(255), nullable=False)
    sent_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    sent_by: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    recipient_count: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    email_type: Mapped[EmailLogsEmailType] = mapped_column(
        Enum(EmailLogsEmailType, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    data: Mapped[Optional[dict]] = mapped_column(JSON)

    member: Mapped[Optional["Members"]] = relationship("Members", back_populates="email_logs", foreign_keys=[member_id])
    event: Mapped[Optional["Events"]] = relationship("Events", back_populates="email_logs")
    sender: Mapped["Members"] = relationship("Members", foreign_keys=[sent_by], passive_deletes=True)


class EmailJobs(Base):
    """One row per background email send.

    These jobs run after the response is sent, so a failure has nowhere to go:
    the caller already has its 200. This table is where a job's outcome lives,
    and what GET /emails/jobs reads.
    """

    __tablename__ = "email_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by"], ["members.id"], ondelete="CASCADE", onupdate="CASCADE", name="fk_email_jobs_created_by"
        ),
        ForeignKeyConstraint(
            ["event_id"], ["events.id"], ondelete="CASCADE", onupdate="CASCADE", name="fk_email_jobs_event"
        ),
        Index("fk_email_jobs_created_by", "created_by"),
        Index("fk_email_jobs_event", "event_id"),
        Index("ix_email_jobs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    job_type: Mapped[EmailJobsType] = mapped_column(
        Enum(EmailJobsType, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    status: Mapped[EmailJobsStatus] = mapped_column(
        Enum(EmailJobsStatus, values_callable=lambda cls: [member.value for member in cls]),
        nullable=False,
        server_default=text("'queued'"),
    )
    created_by: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    event_id: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))
    total: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False, server_default=text("'0'"))
    succeeded: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False, server_default=text("'0'"))
    failed: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False, server_default=text("'0'"))
    error: Mapped[Optional[str]] = mapped_column(TEXT)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    creator: Mapped["Members"] = relationship("Members", foreign_keys=[created_by], passive_deletes=True)


class FormSyncJobs(Base):
    """One row per Google Forms webhook sync.

    `app.services.form_sync.sync_form_submissions` runs after the webhook's
    response is sent, same as the email jobs above, and used to only surface a
    failure via `logger.exception` - this table gives it the same
    job_tracker.track() treatment `email_jobs` has: a status a caller can
    actually poll. `total` is set once the sync knows how many responses it has
    to match; unlike an email job it cannot know that up front.
    """

    __tablename__ = "form_sync_jobs"
    __table_args__ = (Index("ix_form_sync_jobs_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    google_form_id: Mapped[str] = mapped_column(VARCHAR(64, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    status: Mapped[FormSyncJobsStatus] = mapped_column(
        Enum(FormSyncJobsStatus, values_callable=lambda cls: [member.value for member in cls]),
        nullable=False,
        server_default=text("'queued'"),
    )
    total: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False, server_default=text("'0'"))
    succeeded: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False, server_default=text("'0'"))
    failed: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False, server_default=text("'0'"))
    error: Mapped[Optional[str]] = mapped_column(TEXT)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class EmailTemplates(Base):
    __tablename__ = "email_templates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by"], ["members.id"], ondelete="CASCADE", onupdate="CASCADE", name="fk_email_templates_created_by"
        ),
        Index("name", "name", unique=True),
        Index("fk_email_templates_created_by", "created_by"),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    name: Mapped[str] = mapped_column(VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False)
    subject: Mapped[str] = mapped_column(
        VARCHAR(255, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
    )
    html_content: Mapped[str] = mapped_column(
        LONGTEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
    )
    preview_text: Mapped[Optional[str]] = mapped_column(VARCHAR(255, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    created_by: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    creator: Mapped["Members"] = relationship("Members", foreign_keys=[created_by], passive_deletes=True)


class Semesters(Base):
    """An academic term. Events (and, later, the club structure) point at it by ``id``.

    A semester is described by three facts an admin enters - ``term``,
    ``hijri_year`` and ``academic_year_start`` - and MySQL derives the rest, so
    a code can never disagree with its term:

    - ``hijri_code``: the university's number, e.g. 471 / 472 / 475. The Hijri
      year's last two digits, then 1 (first), 2 (second) or 5 (summer).
    - ``gregorian_code``: ours, e.g. 251 / 252 / 253. The academic start year's
      last two digits, then 1, 2 or 3.
    - ``name``: "Fall 2025" / "Spring 2026" / "Summer 2026".

    There is no "current" flag: the current semester is the most recent one
    that has started (see ``app/semesters.py``). ``is_public`` rows are readable
    by anyone, the rest require super admin credentials.
    """

    __tablename__ = "semesters"
    __table_args__ = (
        Index("uq_semesters_hijri_code", "hijri_code", unique=True),
        Index("uq_semesters_gregorian_code", "gregorian_code", unique=True),
        Index("uq_semesters_hijri_year_term", "hijri_year", "term", unique=True),
        CheckConstraint("end_date >= start_date", name="ck_semesters_dates"),
    )

    id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True, default=lambda: str(uuid.uuid4()))
    term: Mapped[SemesterTerm] = mapped_column(
        Enum(SemesterTerm, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    hijri_year: Mapped[int] = mapped_column(SMALLINT(unsigned=True), nullable=False)
    academic_year_start: Mapped[int] = mapped_column(SMALLINT(unsigned=True), nullable=False)
    # Same expressions as migration d1e2f3a4b5c6.
    hijri_code: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True),
        Computed(
            "(hijri_year % 100) * 10 + CASE term WHEN 'first' THEN 1 WHEN 'second' THEN 2 ELSE 5 END", persisted=True
        ),
    )
    gregorian_code: Mapped[int] = mapped_column(
        SMALLINT(unsigned=True),
        Computed(
            "(academic_year_start % 100) * 10 + CASE term WHEN 'first' THEN 1 WHEN 'second' THEN 2 ELSE 3 END",
            persisted=True,
        ),
    )
    name: Mapped[str] = mapped_column(
        String(20),
        Computed(
            "CONCAT(CASE term WHEN 'first' THEN 'Fall' WHEN 'second' THEN 'Spring' ELSE 'Summer' END, ' ', "
            "academic_year_start + (term <> 'first'))",
            persisted=True,
        ),
    )
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    is_public: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'1'"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")
    )


class SuperAdmins(Base):
    """A member who can do anything, to any department. Anyone can be one; super admins add each other."""

    __tablename__ = "super_admins"
    __table_args__ = (
        ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_super_admins_member", ondelete="RESTRICT"),
        ForeignKeyConstraint(["added_by"], ["members.id"], name="fk_super_admins_added_by", ondelete="SET NULL"),
    )

    member_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True, autoincrement=False)
    # NULL when added by scripts/add_super_admin.py, or when the adder's row is gone.
    added_by: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))
    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    member: Mapped["Members"] = relationship("Members", foreign_keys=[member_id])


class SharedPermissions(Base):
    """A permission every leader and VP has, whatever their department.

    ``permission`` is a key from ``app/services/permissions/catalogue.py``.
    """

    __tablename__ = "shared_permissions"
    __table_args__ = (
        ForeignKeyConstraint(["added_by"], ["members.id"], name="fk_shared_permissions_added_by", ondelete="SET NULL"),
    )

    permission: Mapped[str] = mapped_column(String(64), primary_key=True)
    added_by: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))
    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class DepartmentPermissions(Base):
    """A permission one department's leaders and VPs have because of that department's job."""

    __tablename__ = "department_permissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_department_permissions_department", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["added_by"], ["members.id"], name="fk_department_permissions_added_by", ondelete="SET NULL"
        ),
    )

    department_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    permission: Mapped[str] = mapped_column(String(64), primary_key=True)
    added_by: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))
    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class PermissionGrants(Base):
    """A permission a leader or VP gave one member of their department, for one semester.

    Only counts while the semester is current and the member is still on that
    department's roster. Revoking keeps the row. At most one active grant per
    semester, department, member and permission: the generated key is NULL
    once a grant is revoked, and unique while it is not.
    """

    __tablename__ = "permission_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["semester_id"], ["semesters.id"], name="fk_permission_grants_semester", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_permission_grants_department", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_permission_grants_member", ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["granted_by"], ["members.id"], name="fk_permission_grants_granted_by", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["revoked_by"], ["members.id"], name="fk_permission_grants_revoked_by", ondelete="RESTRICT"
        ),
        Index(
            "uq_permission_grants_active",
            "semester_id",
            "department_id",
            "member_id",
            "permission",
            "active_key",
            unique=True,
        ),
        Index("ix_permission_grants_member", "member_id", "semester_id"),
        Index("ix_permission_grants_department", "department_id"),
    )

    id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True, default=lambda: str(uuid.uuid4()))
    semester_id: Mapped[str] = mapped_column(UUID_CHAR, nullable=False)
    department_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    member_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    permission: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_by: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    granted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    revoked_by: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))
    active_key: Mapped[Optional[int]] = mapped_column(
        TINYINT(unsigned=True), Computed("CASE WHEN revoked_at IS NULL THEN 1 END", persisted=True)
    )

    member: Mapped["Members"] = relationship("Members", foreign_keys=[member_id])
    granter: Mapped["Members"] = relationship("Members", foreign_keys=[granted_by])
    revoker: Mapped[Optional["Members"]] = relationship("Members", foreign_keys=[revoked_by])


class PipelineTeam(str, enum.Enum):
    """The three departments every event request passes through."""

    DESIGN = "design"
    LOGISTICS = "logistics"
    MEDIA = "media"


class PipelineLocks(Base):
    """One row per lock the pipeline takes with ``SELECT ... FOR UPDATE``: ``booking`` and ``sweep``.

    A row lock ends with its transaction, unlike a named ``GET_LOCK``, which
    belongs to a pooled connection and could outlive the commit.
    """

    __tablename__ = "pipeline_locks"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)


class BookingBans(Base):
    """A day Logistics closed to bookings, e.g. exams. Every team sees the reason."""

    __tablename__ = "booking_bans"
    __table_args__ = (
        ForeignKeyConstraint(["banned_by"], ["members.id"], name="fk_booking_bans_banned_by", ondelete="RESTRICT"),
    )

    date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    reason: Mapped[Optional[str]] = mapped_column(VARCHAR(200, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    banned_by: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class EventRequestStage(str, enum.Enum):
    """Where an event request is. The whole path is here from the start; see docs/EVENTS_PIPELINE.md."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    RETURNED = "returned"
    MEDIA = "media"
    READY = "ready"
    PUBLISHED = "published"
    CANCELLED = "cancelled"


class EventRequestUndatedReason(str, enum.Enum):
    HOLD_EXPIRED = "hold_expired"
    DAY_BANNED = "day_banned"


class EventRequestType(str, enum.Enum):
    COURSE = "course"
    BOOTCAMP = "bootcamp"
    MEETUP = "meetup"
    WORKSHOP = "workshop"
    COMPETITION = "competition"


class EventRequestLocationScope(str, enum.Enum):
    INSIDE = "inside"
    OUTSIDE = "outside"


class EventRequestAudience(str, enum.Enum):
    MALE = "male"
    FEMALE = "female"
    MIXED = "mixed"
    NONE = "none"


class EventRequestRegistration(str, enum.Enum):
    ACCEPTANCE = "acceptance"
    OPEN = "open"
    NONE = "none"


def _enum(cls):
    return Enum(cls, values_callable=lambda members: [member.value for member in members])


class EventRequests(Base):
    """A department's request to hold an event, from booking its dates to publishing it.

    The dates are held for 24 hours while the request is a draft
    (``hold_expires_at``), and stay taken from submit until it is published. A
    request that loses its dates keeps everything else and says why in
    ``undated_reason``. The event details are real columns because the
    calendar and publish read them; they are all nullable while the request is
    a draft and checked on submit.
    """

    __tablename__ = "event_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_event_requests_department", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(["created_by"], ["members.id"], name="fk_event_requests_created_by", ondelete="RESTRICT"),
        ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_event_requests_event", ondelete="SET NULL"),
        CheckConstraint("end_date >= start_date", name="ck_event_requests_dates"),
        Index("ix_event_requests_dates", "start_date", "end_date"),
        Index("ix_event_requests_department_stage", "department_id", "stage"),
        Index("ix_event_requests_stage", "stage"),
    )

    id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    created_by: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    stage: Mapped[EventRequestStage] = mapped_column(
        _enum(EventRequestStage), nullable=False, server_default=text("'draft'")
    )
    start_date: Mapped[Optional[datetime.date]] = mapped_column(Date)
    end_date: Mapped[Optional[datetime.date]] = mapped_column(Date)
    hold_expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    undated_reason: Mapped[Optional[EventRequestUndatedReason]] = mapped_column(_enum(EventRequestUndatedReason))

    title: Mapped[Optional[str]] = mapped_column(VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    description: Mapped[Optional[str]] = mapped_column(TEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    event_type: Mapped[Optional[EventRequestType]] = mapped_column(_enum(EventRequestType))
    presenter_name: Mapped[Optional[str]] = mapped_column(
        VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
    )
    presenter_email: Mapped[Optional[str]] = mapped_column(String(150))
    # {"2026-10-12": "on_site", "2026-10-13": "online"} - one entry per booked day.
    day_modes: Mapped[Optional[dict]] = mapped_column(JSON)
    daily_start_time: Mapped[Optional[datetime.time]] = mapped_column(Time)
    daily_end_time: Mapped[Optional[datetime.time]] = mapped_column(Time)
    is_official: Mapped[Optional[int]] = mapped_column(TINYINT(1))
    location_scope: Mapped[Optional[EventRequestLocationScope]] = mapped_column(_enum(EventRequestLocationScope))
    audience: Mapped[Optional[EventRequestAudience]] = mapped_column(_enum(EventRequestAudience))
    registration: Mapped[Optional[EventRequestRegistration]] = mapped_column(_enum(EventRequestRegistration))
    expected_accepted: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))
    help_needed: Mapped[Optional[str]] = mapped_column(TEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")
    )
    submitted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    event_id: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))

    # Design can return a request once, within two days of receiving it; the team then has 12 hours.
    returned_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    return_count: Mapped[int] = mapped_column(TINYINT(unsigned=True), nullable=False, server_default=text("'0'"))
    return_notes: Mapped[Optional[str]] = mapped_column(TEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    return_due_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    department: Mapped["Departments"] = relationship("Departments")
    creator: Mapped["Members"] = relationship("Members")
    partners: Mapped[list["EventRequestPartners"]] = relationship(
        "EventRequestPartners", passive_deletes=True, cascade="all, delete-orphan"
    )
    tasks: Mapped[list["EventRequestTasks"]] = relationship(
        "EventRequestTasks", passive_deletes=True, cascade="all, delete-orphan", order_by="EventRequestTasks.team"
    )


class EventRequestPartners(Base):
    """Another department the event is run with."""

    __tablename__ = "event_request_partners"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id"], ["event_requests.id"], name="fk_event_request_partners_request", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_event_request_partners_department", ondelete="RESTRICT"
        ),
    )

    request_id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True)
    department_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)

    department: Mapped["Departments"] = relationship("Departments")


class EventRequestTaskStatus(str, enum.Enum):
    BRIEF = "brief"  # the requesting team is still writing the brief
    OPEN = "open"  # the team has it
    RETURNED = "returned"  # sent back to the requesting team
    DONE = "done"


class EventRequestTasks(Base):
    """One team's part of a request: its brief (JSON, versioned by a Pydantic model in code) and its progress."""

    __tablename__ = "event_request_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id"], ["event_requests.id"], name="fk_event_request_tasks_request", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["completed_by"], ["members.id"], name="fk_event_request_tasks_completed_by", ondelete="RESTRICT"
        ),
        Index("uq_event_request_tasks_team", "request_id", "team", unique=True),
        Index("ix_event_request_tasks_team_status", "team", "status"),
    )

    id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id: Mapped[str] = mapped_column(UUID_CHAR, nullable=False)
    team: Mapped[PipelineTeam] = mapped_column(_enum(PipelineTeam), nullable=False)
    status: Mapped[EventRequestTaskStatus] = mapped_column(
        _enum(EventRequestTaskStatus), nullable=False, server_default=text("'brief'")
    )
    brief: Mapped[Optional[dict]] = mapped_column(JSON)
    brief_version: Mapped[Optional[int]] = mapped_column(SMALLINT(unsigned=True))
    opened_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    completed_by: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))

    completer: Mapped[Optional["Members"]] = relationship("Members")


class PipelineNotificationKind(str, enum.Enum):
    REQUEST_RECEIVED = "request_received"
    DATES_BANNED = "dates_banned"
    HOLD_EXPIRED = "hold_expired"
    RETURNED = "returned"
    TASK_DONE = "task_done"
    MEDIA_RECEIVED = "media_received"
    READY_TO_PUBLISH = "ready_to_publish"


class PipelineNotifications(Base):
    """Something that happened to a request, shown on the dashboards of one department's pipeline users."""

    __tablename__ = "pipeline_notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_pipeline_notifications_department", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["request_id"], ["event_requests.id"], name="fk_pipeline_notifications_request", ondelete="CASCADE"
        ),
        Index("ix_pipeline_notifications_department", "department_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    request_id: Mapped[str] = mapped_column(UUID_CHAR, nullable=False)
    kind: Mapped[PipelineNotificationKind] = mapped_column(_enum(PipelineNotificationKind), nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    department: Mapped["Departments"] = relationship("Departments")
    request: Mapped["EventRequests"] = relationship("EventRequests")


class PipelineNotificationReads(Base):
    """Each person marks their own notifications read."""

    __tablename__ = "pipeline_notification_reads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["notification_id"],
            ["pipeline_notifications.id"],
            name="fk_pipeline_notification_reads_notification",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_pipeline_notification_reads_member", ondelete="CASCADE"
        ),
    )

    notification_id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True)
    member_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    read_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class PipelinePenalties(Base):
    """Points a department loses for fixing a returned request late.

    One row per request; the sweep grows ``late_days`` while the request stays
    late. It is applied as a discount on the department's log for the real
    event when the request is published (``applied_log_id``), not before.
    """

    __tablename__ = "pipeline_penalties"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id"], ["event_requests.id"], name="fk_pipeline_penalties_request", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_pipeline_penalties_department", ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["applied_log_id"], ["logs.id"], name="fk_pipeline_penalties_log", ondelete="SET NULL"),
        Index("uq_pipeline_penalties_request", "request_id", unique=True),
    )

    id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id: Mapped[str] = mapped_column(UUID_CHAR, nullable=False)
    department_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    late_days: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    points: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")
    )
    applied_log_id: Mapped[Optional[int]] = mapped_column(INTEGER(unsigned=True))


# =============================================================================
# Views (read-only, defined in DB migrations)
# =============================================================================

t_forms_submissions = Table(
    "forms_submissions",
    Base.metadata,
    Column("submission_id", INTEGER(unsigned=True), server_default=text("'0'")),
    Column("submitted_at", DateTime, server_default=text("'CURRENT_TIMESTAMP'")),
    Column("form_type", Enum(FormsSubmissionsFormType, values_callable=lambda cls: [member.value for member in cls])),
    Column(
        "submission_type",
        Enum(FormsSubmissionsSubmissionType, values_callable=lambda cls: [member.value for member in cls]),
    ),
    Column("id", INTEGER(unsigned=True), server_default=text("'0'")),
    Column("name", String(50)),
    Column("email", String(100)),
    Column("phone_number", String(20)),
    Column("uni_id", String(50)),
    Column("gender", Enum(FormsSubmissionsGender, values_callable=lambda cls: [member.value for member in cls])),
    Column("uni_level", Integer),
    Column("uni_college", String(100)),
    Column("is_accepted", TINYINT(1), server_default=text("'0'")),
    Column("is_invited", TINYINT(1), server_default=text("'0'")),
    Column("google_submission_value", JSON),
    Column("event_id", INTEGER(unsigned=True)),
    Column("form_id", INTEGER(unsigned=True), server_default=text("'0'")),
    Column("google_form_id", String(100)),
    info={"is_view": True},
)

t_open_events = Table(
    "open_events",
    Base.metadata,
    Column("id", INTEGER(unsigned=True), server_default=text("'0'")),
    Column("name", String(150)),
    Column("description", Text),
    Column("location_type", Enum(OpenEventsLocationType, values_callable=lambda cls: [member.value for member in cls])),
    Column("location", String(100)),
    Column("start_datetime", DateTime, server_default=text("'CURRENT_TIMESTAMP'")),
    Column("end_datetime", DateTime, server_default=text("'CURRENT_TIMESTAMP'")),
    Column("status", Enum(OpenEventsStatus, values_callable=lambda cls: [member.value for member in cls])),
    Column("image_url", String(500)),
    Column("meeting_url", String(500)),
    Column("is_official", TINYINT(1), server_default=text("'0'")),
    Column("form_id", INTEGER(unsigned=True), server_default=text("'0'")),
    Column("form_type", Enum(OpenEventsFormType, values_callable=lambda cls: [member.value for member in cls])),
    Column("google_responders_url", String(150)),
    info={"is_view": True},
)
