from typing import Optional
import datetime
import enum

from sqlalchemy import Column, DateTime, Enum, ForeignKeyConstraint, Index, Integer, JSON, String, Table, Text, text
from sqlalchemy.dialects.mysql import INTEGER, TEXT, TINYINT, VARCHAR
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


class RoleType(str, enum.Enum):
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"
    ADMIN_POINTS = "admin_points"
    NONE = "none"


class SubmissionsSubmissionType(str, enum.Enum):
    NONE = "none"
    REGISTRATION = "registration"
    PARTIAL = "partial"
    GOOGLE = "google"


class FormsSubmissionsFormType(str, enum.Enum):
    NONE = "none"
    REGISTRATION = "registration"
    GOOGLE = "google"


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


class OpenEventsStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    ACTIVE = "active"
    CLOSED = "closed"


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

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[DepartmentsType] = mapped_column(
        Enum(DepartmentsType, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    ar_name: Mapped[str] = mapped_column(
        VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
    )

    departments_logs: Mapped[list["DepartmentsLogs"]] = relationship(
        "DepartmentsLogs", back_populates="department", passive_deletes=True
    )


class Events(Base):
    __tablename__ = "events"
    __table_args__ = (Index("event_name", "name"), Index("events_id_IDX", "id", "name"))

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
    image_url: Mapped[Optional[str]] = mapped_column(VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"))
    is_official: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    forms: Mapped[list["Forms"]] = relationship("Forms", back_populates="event", passive_deletes=True)
    logs: Mapped[list["Logs"]] = relationship("Logs", back_populates="event", passive_deletes=True)


class Members(Base):
    __tablename__ = "members"
    __table_args__ = (Index("uni_id", "uni_id", unique=True),)

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    uni_id: Mapped[str] = mapped_column(String(50), nullable=False)
    gender: Mapped[MembersGender] = mapped_column(
        Enum(MembersGender, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )
    uni_level: Mapped[int] = mapped_column(Integer, nullable=False)
    uni_college: Mapped[str] = mapped_column(
        VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
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

    role: Mapped[list["Role"]] = relationship("Role", back_populates="member", passive_deletes=True)
    members_logs: Mapped[list["MembersLogs"]] = relationship("MembersLogs", back_populates="member")
    submissions: Mapped[list["Submissions"]] = relationship(
        "Submissions", back_populates="member", passive_deletes=True
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
    google_refresh_token: Mapped[Optional[str]] = mapped_column(
        VARCHAR(500, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
    )
    google_watch_id: Mapped[Optional[str]] = mapped_column(String(100))
    google_responders_url: Mapped[Optional[str]] = mapped_column(
        VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
    )

    event: Mapped["Events"] = relationship("Events", back_populates="forms")
    submissions: Mapped[list["Submissions"]] = relationship("Submissions", back_populates="form", passive_deletes=True)


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


class Role(Base):
    __tablename__ = "role"
    __table_args__ = (
        ForeignKeyConstraint(
            ["member_id"], ["members.id"], ondelete="CASCADE", onupdate="CASCADE", name="fk_role_member"
        ),
        Index("fk_role_member", "member_id"),
    )

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True)
    member_id: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    role: Mapped[RoleType] = mapped_column(
        Enum(RoleType, values_callable=lambda cls: [member.value for member in cls]), nullable=False
    )

    member: Mapped["Members"] = relationship("Members", back_populates="role")


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
    Column("image_url", String(100)),
    Column("is_official", TINYINT(1), server_default=text("'0'")),
    Column("form_id", INTEGER(unsigned=True), server_default=text("'0'")),
    Column("form_type", Enum(OpenEventsFormType, values_callable=lambda cls: [member.value for member in cls])),
    Column("google_responders_url", String(150)),
    info={"is_view": True},
)
