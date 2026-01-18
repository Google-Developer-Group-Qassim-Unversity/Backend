from typing import Optional
import datetime

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKeyConstraint, Index, JSON, String, Table, Text, text
from sqlalchemy.dialects.mysql import ENUM, INTEGER, TINYINT, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class Actions(Base):
    __tablename__ = 'actions'

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    action_name: Mapped[str] = mapped_column(VARCHAR(60), nullable=False)
    points: Mapped[int] = mapped_column(INTEGER, nullable=False)
    action_type: Mapped[str] = mapped_column(Enum('composite', 'department', 'member', 'bonus'), nullable=False)
    action_description: Mapped[Optional[str]] = mapped_column(VARCHAR(100))
    arabic_action_name: Mapped[Optional[str]] = mapped_column(VARCHAR(100))

    logs: Mapped[list['Logs']] = relationship('Logs', back_populates='action')


class Departments(Base):
    __tablename__ = 'departments'
    __table_args__ = (
        Index('departments_name_IDX', 'name'),
        Index('departments_type_IDX', 'type')
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(Enum('administrative', 'practical'), nullable=False)

    departments_logs: Mapped[list['DepartmentsLogs']] = relationship('DepartmentsLogs', back_populates='department')


class Events(Base):
    __tablename__ = 'events'
    __table_args__ = (
        Index('event_name', 'name'),
        Index('events_id_IDX', 'id', 'name'),
        Index('events_unique', 'name', unique=True)
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    name: Mapped[str] = mapped_column(VARCHAR(150), nullable=False)
    location_type: Mapped[str] = mapped_column(ENUM('online', 'on-site', 'none'), nullable=False)
    location: Mapped[str] = mapped_column(String(100), nullable=False)
    start_datetime: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text("'2025-01-01 00:00:00'"))
    end_datetime: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text("'2025-01-01 00:00:00'"))
    status: Mapped[str] = mapped_column(ENUM('announced', 'open', 'closed'), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    image_url: Mapped[Optional[str]] = mapped_column(String(100))
    is_official: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))

    forms: Mapped[list['Forms']] = relationship('Forms', back_populates='event')
    logs: Mapped[list['Logs']] = relationship('Logs', back_populates='event')


t_forms_submissions = Table(
    'forms_submissions', Base.metadata,
    Column('form_id', INTEGER),
    Column('form_type', Enum('google', 'none')),
    Column('member_id', INTEGER),
    Column('is_accepted', TINYINT(1), server_default=text("'0'")),
    Column('google_submission_id', String(100)),
    Column('google_submission_value', JSON),
    Column('submitted_at', DateTime, server_default=text("'CURRENT_TIMESTAMP'")),
    Column('id', INTEGER, server_default=text("'0'")),
    Column('event_id', INTEGER),
    Column('google_form_id', String(100))
)


class Members(Base):
    __tablename__ = 'members'
    __table_args__ = (
        Index('uni_id', 'uni_id', unique=True),
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    uni_id: Mapped[str] = mapped_column(String(9), nullable=False)
    gender: Mapped[str] = mapped_column(Enum('Male', 'Female'), nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(20))

    admins: Mapped[list['Admins']] = relationship('Admins', back_populates='member')
    members_logs: Mapped[list['MembersLogs']] = relationship('MembersLogs', back_populates='member')
    submissions: Mapped[list['Submissions']] = relationship('Submissions', back_populates='member')


t_open_events = Table(
    'open_events', Base.metadata,
    Column('id', INTEGER, server_default=text("'0'")),
    Column('name', String(150)),
    Column('description', Text),
    Column('location_type', Enum('online', 'on-site', 'none')),
    Column('location', String(100)),
    Column('start_datetime', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('end_datetime', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('status', Enum('announced', 'open', 'closed')),
    Column('image_url', String(100)),
    Column('is_official', TINYINT(1), server_default=text("'0'")),
    Column('form_id', INTEGER, server_default=text("'0'")),
    Column('form_type', Enum('google', 'none')),
    Column('google_responders_url', String(100))
)


class Admins(Base):
    __tablename__ = 'admins'
    __table_args__ = (
        ForeignKeyConstraint(['member_id'], ['members.id'], name='admins_members_FK'),
        Index('admins_unique', 'member_id', unique=True)
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    can_give_points: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'0'"))
    member_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    google_refresh_token: Mapped[Optional[str]] = mapped_column(String(100))

    member: Mapped['Members'] = relationship('Members', back_populates='admins')


class Forms(Base):
    __tablename__ = 'forms'
    __table_args__ = (
        ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE', onupdate='CASCADE', name='forms_ibfk_1'),
        Index('forms_unique_event_id', 'event_id', unique=True)
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    event_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    form_type: Mapped[str] = mapped_column(Enum('google', 'none'), nullable=False)
    google_form_id: Mapped[Optional[str]] = mapped_column(VARCHAR(100))
    google_refresh_token: Mapped[Optional[str]] = mapped_column(VARCHAR(500))
    google_watch_id: Mapped[Optional[str]] = mapped_column(String(100))
    google_responders_url: Mapped[Optional[str]] = mapped_column(String(100))

    event: Mapped['Events'] = relationship('Events', back_populates='forms')
    submissions: Mapped[list['Submissions']] = relationship('Submissions', back_populates='form')


class Logs(Base):
    __tablename__ = 'logs'
    __table_args__ = (
        ForeignKeyConstraint(['action_id'], ['actions.id'], ondelete='CASCADE', onupdate='CASCADE', name='logs_ibfk_1'),
        ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE', name='fk_events'),
        Index('action_id', 'action_id'),
        Index('action_id_event_id_idx', 'action_id', 'event_id'),
        Index('fk_events', 'event_id')
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    action_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    event_id: Mapped[Optional[int]] = mapped_column(INTEGER)

    action: Mapped['Actions'] = relationship('Actions', back_populates='logs')
    event: Mapped[Optional['Events']] = relationship('Events', back_populates='logs')
    departments_logs: Mapped[list['DepartmentsLogs']] = relationship('DepartmentsLogs', back_populates='log')
    members_logs: Mapped[list['MembersLogs']] = relationship('MembersLogs', back_populates='log')
    modifications: Mapped[list['Modifications']] = relationship('Modifications', back_populates='log')


class DepartmentsLogs(Base):
    __tablename__ = 'departments_logs'
    __table_args__ = (
        ForeignKeyConstraint(['department_id'], ['departments.id'], ondelete='CASCADE', onupdate='CASCADE', name='departments_logs_departments_FK'),
        ForeignKeyConstraint(['log_id'], ['logs.id'], ondelete='CASCADE', onupdate='CASCADE', name='departments_logs_logs_FK'),
        Index('departments_logs_departments_FK', 'department_id'),
        Index('departments_logs_unique', 'log_id', 'department_id', unique=True)
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    department_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    log_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    attendants_number: Mapped[Optional[int]] = mapped_column(INTEGER, server_default=text("'0'"))

    department: Mapped['Departments'] = relationship('Departments', back_populates='departments_logs')
    log: Mapped['Logs'] = relationship('Logs', back_populates='departments_logs')


class MembersLogs(Base):
    __tablename__ = 'members_logs'
    __table_args__ = (
        ForeignKeyConstraint(['log_id'], ['logs.id'], ondelete='CASCADE', onupdate='CASCADE', name='members_logs_logs_FK'),
        ForeignKeyConstraint(['member_id'], ['members.id'], name='fk_members_id'),
        Index('fk_members_id', 'member_id'),
        Index('members_logs_unique', 'log_id', 'member_id', unique=True)
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    member_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    log_id: Mapped[int] = mapped_column(INTEGER, nullable=False)

    log: Mapped['Logs'] = relationship('Logs', back_populates='members_logs')
    member: Mapped['Members'] = relationship('Members', back_populates='members_logs')
    absence: Mapped[list['Absence']] = relationship('Absence', back_populates='member_log')


class Modifications(Base):
    __tablename__ = 'modifications'
    __table_args__ = (
        ForeignKeyConstraint(['log_id'], ['logs.id'], ondelete='CASCADE', onupdate='CASCADE', name='modifications_ibfk_1'),
        Index('log_id', 'log_id')
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    log_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    type: Mapped[str] = mapped_column(Enum('bonus', 'discount'), nullable=False)
    value: Mapped[int] = mapped_column(INTEGER, nullable=False)

    log: Mapped['Logs'] = relationship('Logs', back_populates='modifications')


class Submissions(Base):
    __tablename__ = 'submissions'
    __table_args__ = (
        ForeignKeyConstraint(['form_id'], ['forms.id'], ondelete='CASCADE', onupdate='CASCADE', name='submissions_ibfk_1'),
        ForeignKeyConstraint(['member_id'], ['members.id'], ondelete='CASCADE', onupdate='CASCADE', name='submissions_ibfk_2'),
        Index('from_id_member_id_idx', 'form_id', 'member_id'),
        Index('submissions_unique', 'member_id', 'form_id', unique=True)
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    form_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    member_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    is_accepted: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'0'"))
    submitted_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    submission_type: Mapped[str] = mapped_column(Enum('none', 'partial', 'google'), nullable=False)
    google_submission_id: Mapped[Optional[str]] = mapped_column(String(100))
    google_submission_value: Mapped[Optional[dict]] = mapped_column(JSON)

    form: Mapped['Forms'] = relationship('Forms', back_populates='submissions')
    member: Mapped['Members'] = relationship('Members', back_populates='submissions')


class Absence(Base):
    __tablename__ = 'absence'
    __table_args__ = (
        ForeignKeyConstraint(['member_log_id'], ['members_logs.id'], ondelete='CASCADE', onupdate='CASCADE', name='absence_members_logs_FK'),
        Index('absence_members_logs_FK', 'member_log_id')
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    member_log_id: Mapped[int] = mapped_column(INTEGER, nullable=False)

    member_log: Mapped['MembersLogs'] = relationship('MembersLogs', back_populates='absence')
