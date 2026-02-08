from typing import Optional
import datetime

from sqlalchemy import Column, DECIMAL, DateTime, Enum, ForeignKeyConstraint, Index, Integer, JSON, String, Table, Text, text
from sqlalchemy.dialects.mysql import ENUM, INTEGER, TEXT, TINYINT, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class Actions(Base):
    __tablename__ = 'actions'

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    action_name: Mapped[str] = mapped_column(VARCHAR(60), nullable=False)
    points: Mapped[int] = mapped_column(INTEGER, nullable=False)
    action_type: Mapped[str] = mapped_column(ENUM('composite', 'department', 'member', 'bonus'), nullable=False)
    ar_action_name: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)

    logs: Mapped[list['Logs']] = relationship('Logs', back_populates='action')


class Departments(Base):
    __tablename__ = 'departments'

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(Enum('administrative', 'practical'), nullable=False)
    ar_name: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)

    departments_logs: Mapped[list['DepartmentsLogs']] = relationship('DepartmentsLogs', back_populates='department')


t_departments_points = Table(
    'departments_points', Base.metadata,
    Column('department_id', INTEGER, server_default=text("'0'")),
    Column('department_name', String(50)),
    Column('department_type', Enum('administrative', 'practical')),
    Column('ar_department_name', String(100)),
    Column('total_points', DECIMAL(55, 0), server_default=text("'0'"))
)


t_departments_points_history = Table(
    'departments_points_history', Base.metadata,
    Column('department_id', INTEGER),
    Column('department_name', String(50)),
    Column('ar_department_name', String(100)),
    Column('event_id', INTEGER),
    Column('event_name', String(150)),
    Column('start_datetime', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('end_datetime', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('action_name', String(60)),
    Column('ar_action_name', String(100)),
    Column('points', DECIMAL(54, 0))
)


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
    location: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    start_datetime: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text("'2025-01-01 00:00:00'"))
    end_datetime: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text("'2025-01-01 00:00:00'"))
    status: Mapped[str] = mapped_column(ENUM('draft', 'open', 'active', 'closed'), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(TEXT)
    image_url: Mapped[Optional[str]] = mapped_column(VARCHAR(100))
    is_official: Mapped[Optional[int]] = mapped_column(TINYINT(1), server_default=text("'0'"))

    forms: Mapped[list['Forms']] = relationship('Forms', back_populates='event')
    logs: Mapped[list['Logs']] = relationship('Logs', back_populates='event')


t_forms_submissions = Table(
    'forms_submissions', Base.metadata,
    Column('submission_id', INTEGER, server_default=text("'0'")),
    Column('submitted_at', DateTime, server_default=text("'CURRENT_TIMESTAMP'")),
    Column('form_type', Enum('none', 'registration', 'google')),
    Column('submission_type', Enum('none', 'registration', 'partial', 'google')),
    Column('id', INTEGER, server_default=text("'0'")),
    Column('name', String(50)),
    Column('email', String(100)),
    Column('phone_number', String(20)),
    Column('uni_id', String(50)),
    Column('gender', Enum('Male', 'Female')),
    Column('uni_level', Integer),
    Column('uni_college', String(100)),
    Column('is_accepted', TINYINT(1), server_default=text("'0'")),
    Column('google_submission_value', JSON),
    Column('event_id', INTEGER),
    Column('form_id', INTEGER, server_default=text("'0'")),
    Column('google_form_id', String(100))
)


t_member_event_history = Table(
    'member_event_history', Base.metadata,
    Column('member_id', INTEGER, server_default=text("'0'")),
    Column('member_name', String(50)),
    Column('event_id', INTEGER, server_default=text("'0'")),
    Column('event_name', String(150)),
    Column('start_datetime', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('end_datetime', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('points', DECIMAL(54, 0)),
    Column('action_name', Text),
    Column('ar_action_name', Text)
)


class Members(Base):
    __tablename__ = 'members'
    __table_args__ = (
        Index('uni_id', 'uni_id', unique=True),
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    uni_id: Mapped[str] = mapped_column(String(50), nullable=False)
    gender: Mapped[str] = mapped_column(Enum('Male', 'Female'), nullable=False)
    uni_level: Mapped[int] = mapped_column(Integer, nullable=False)
    uni_college: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    is_authenticated: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("'0'"))
    email: Mapped[Optional[str]] = mapped_column(String(100))
    phone_number: Mapped[Optional[str]] = mapped_column(String(20))

    members_logs: Mapped[list['MembersLogs']] = relationship('MembersLogs', back_populates='member')
    submissions: Mapped[list['Submissions']] = relationship('Submissions', back_populates='member')


t_members_points = Table(
    'members_points', Base.metadata,
    Column('member_id', INTEGER, server_default=text("'0'")),
    Column('member_name', String(50)),
    Column('total_points', DECIMAL(54, 0), server_default=text("'0'"))
)


t_open_events = Table(
    'open_events', Base.metadata,
    Column('id', INTEGER, server_default=text("'0'")),
    Column('name', String(150)),
    Column('description', Text),
    Column('location_type', Enum('online', 'on-site', 'none')),
    Column('location', String(100)),
    Column('start_datetime', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('end_datetime', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('status', Enum('draft', 'open', 'active', 'closed')),
    Column('image_url', String(100)),
    Column('is_official', TINYINT(1), server_default=text("'0'")),
    Column('form_id', INTEGER, server_default=text("'0'")),
    Column('form_type', Enum('none', 'registration', 'google')),
    Column('google_responders_url', String(150))
)


class Forms(Base):
    __tablename__ = 'forms'
    __table_args__ = (
        ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE', onupdate='CASCADE', name='forms_ibfk_1'),
        Index('forms_unique_event_id', 'event_id', unique=True)
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    event_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    form_type: Mapped[str] = mapped_column(ENUM('none', 'registration', 'google'), nullable=False)
    google_form_id: Mapped[Optional[str]] = mapped_column(VARCHAR(100))
    google_refresh_token: Mapped[Optional[str]] = mapped_column(VARCHAR(500))
    google_watch_id: Mapped[Optional[str]] = mapped_column(String(100))
    google_responders_url: Mapped[Optional[str]] = mapped_column(VARCHAR(150))

    event: Mapped['Events'] = relationship('Events', back_populates='forms')
    submissions: Mapped[list['Submissions']] = relationship('Submissions', back_populates='form')


class Logs(Base):
    __tablename__ = 'logs'
    __table_args__ = (
        ForeignKeyConstraint(['action_id'], ['actions.id'], ondelete='CASCADE', onupdate='CASCADE', name='logs_ibfk_1'),
        ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE', name='fk_events'),
        Index('action_id', 'action_id'),
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
        Index('departments_logs_idx', 'log_id', 'department_id')
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    department_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    log_id: Mapped[int] = mapped_column(INTEGER, nullable=False)

    department: Mapped['Departments'] = relationship('Departments', back_populates='departments_logs')
    log: Mapped['Logs'] = relationship('Logs', back_populates='departments_logs')


class MembersLogs(Base):
    __tablename__ = 'members_logs'
    __table_args__ = (
        ForeignKeyConstraint(['log_id'], ['logs.id'], ondelete='CASCADE', onupdate='CASCADE', name='members_logs_logs_FK'),
        ForeignKeyConstraint(['member_id'], ['members.id'], name='fk_members_id'),
        Index('fk_members_id', 'member_id'),
        Index('idx_members_logs_log_id', 'log_id'),
        Index('unique_member_log_day', 'member_id', 'log_id', 'date', unique=True)
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    member_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    log_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    date: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    log: Mapped['Logs'] = relationship('Logs', back_populates='members_logs')
    member: Mapped['Members'] = relationship('Members', back_populates='members_logs')


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
    submission_type: Mapped[str] = mapped_column(ENUM('none', 'registration', 'partial', 'google'), nullable=False)
    google_submission_id: Mapped[Optional[str]] = mapped_column(String(100))
    google_submission_value: Mapped[Optional[dict]] = mapped_column(JSON)

    form: Mapped['Forms'] = relationship('Forms', back_populates='submissions')
    member: Mapped['Members'] = relationship('Members', back_populates='submissions')
