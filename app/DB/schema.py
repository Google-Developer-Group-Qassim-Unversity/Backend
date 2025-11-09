from typing import Optional
import datetime

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKeyConstraint, Index, JSON, String, Table, Text, text
from sqlalchemy.dialects.mysql import DATETIME, INTEGER, TEXT, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class PrismaMigrations(Base):
    __tablename__ = '_prisma_migrations'

    id: Mapped[str] = mapped_column(VARCHAR(36), primary_key=True)
    checksum: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    migration_name: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(DATETIME(fsp=3), nullable=False, server_default=text('CURRENT_TIMESTAMP(3)'))
    applied_steps_count: Mapped[int] = mapped_column(INTEGER, nullable=False, server_default=text("'0'"))
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DATETIME(fsp=3))
    logs: Mapped[Optional[str]] = mapped_column(TEXT)
    rolled_back_at: Mapped[Optional[datetime.datetime]] = mapped_column(DATETIME(fsp=3))


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

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(Enum('administrative', 'practical'), nullable=False)

    departments_logs: Mapped[list['DepartmentsLogs']] = relationship('DepartmentsLogs', back_populates='department')


t_departments_points = Table(
    'departments_points', Base.metadata,
    Column('department_id', INTEGER, server_default=text("'0'")),
    Column('department_name', String(50)),
    Column('department_log_id', INTEGER, server_default=text("'0'")),
    Column('log_id', INTEGER, server_default=text("'0'")),
    Column('start_date', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('end_date', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('event_name', String(150)),
    Column('action_points', INTEGER),
    Column('action_name', String(60))
)


class Events(Base):
    __tablename__ = 'events'
    __table_args__ = (
        Index('event_name', 'name'),
        Index('events_unique', 'name', unique=True)
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    name: Mapped[str] = mapped_column(VARCHAR(150), nullable=False)
    location_type: Mapped[str] = mapped_column(Enum('online', 'on-site'), nullable=False)
    location: Mapped[str] = mapped_column(String(100), nullable=False)
    start_datetime: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text("'2025-01-01 00:00:00'"))
    end_datetime: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=text("'2025-01-01 00:00:00'"))
    description: Mapped[Optional[str]] = mapped_column(Text)

    forms: Mapped[list['Forms']] = relationship('Forms', back_populates='event')
    logs: Mapped[list['Logs']] = relationship('Logs', back_populates='event', passive_deletes=True)


t_expanded_logs = Table(
    'expanded logs', Base.metadata,
    Column('log_id', INTEGER, server_default=text("'0'")),
    Column('event_id', INTEGER),
    Column('action_id', INTEGER),
    Column('event_name', String(150)),
    Column('event_description', Text),
    Column('event_start', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('event_end', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('action_name', String(60)),
    Column('action_points', INTEGER),
    Column('action_type', Enum('composite', 'department', 'member', 'bonus'))
)


t_expanded_members_logs = Table(
    'expanded_members_logs', Base.metadata,
    Column('log_id', INTEGER, server_default=text("'0'")),
    Column('event_name', String(150)),
    Column('action_name', String(60)),
    Column('action_type', Enum('composite', 'department', 'member', 'bonus')),
    Column('members', JSON)
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
    email: Mapped[Optional[str]] = mapped_column(String(100))
    phone_number: Mapped[Optional[str]] = mapped_column(String(20))

    members_logs: Mapped[list['MembersLogs']] = relationship('MembersLogs', back_populates='member')
    responses: Mapped[list['Responses']] = relationship('Responses', back_populates='member')


t_members_points = Table(
    'members_points', Base.metadata,
    Column('member_id', INTEGER, server_default=text("'0'")),
    Column('member_name', String(50)),
    Column('member_log_id', INTEGER, server_default=text("'0'")),
    Column('member_gender', Enum('Male', 'Female')),
    Column('log_id', INTEGER, server_default=text("'0'")),
    Column('event_name', String(150)),
    Column('start_date', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('end_date', DateTime, server_default=text("'2025-01-01 00:00:00'")),
    Column('action_points', INTEGER),
    Column('action_name', String(60))
)


class Forms(Base):
    __tablename__ = 'forms'
    __table_args__ = (
        ForeignKeyConstraint(['event_id'], ['events.id'], name='forms_ibfk_1'),
        Index('event_id', 'event_id')
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    event_id: Mapped[Optional[int]] = mapped_column(INTEGER)

    event: Mapped[Optional['Events']] = relationship('Events', back_populates='forms')
    questions: Mapped[list['Questions']] = relationship('Questions', back_populates='forms')


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


class Questions(Base):
    __tablename__ = 'questions'
    __table_args__ = (
        ForeignKeyConstraint(['forms_id'], ['forms.id'], name='questions_ibfk_1'),
        Index('forms_id', 'forms_id')
    )

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    forms_id: Mapped[Optional[int]] = mapped_column(INTEGER)

    forms: Mapped[Optional['Forms']] = relationship('Forms', back_populates='questions')
    responses: Mapped[list['Responses']] = relationship('Responses', back_populates='question')


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


class Responses(Base):
    __tablename__ = 'responses'
    __table_args__ = (
        ForeignKeyConstraint(['member_id'], ['members.id'], name='responses_ibfk_1'),
        ForeignKeyConstraint(['question_id'], ['questions.id'], name='responses_ibfk_2'),
        Index('question_id', 'question_id')
    )

    member_id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    question_id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    member: Mapped['Members'] = relationship('Members', back_populates='responses')
    question: Mapped['Questions'] = relationship('Questions', back_populates='responses')
