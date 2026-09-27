"""Shared set-up for the events pipeline tests: people, rosters, teams, sign-in and a frozen clock."""

from datetime import datetime

import pytest
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from sqlalchemy import select

from app.config import config
from app.DB.club_structure import get_role_by_key
from app.DB.schema import (
    ClubMemberships,
    Departments,
    DepartmentsType,
    Members,
    MembersGender,
    PipelineTeam,
    PipelineTeams,
    SemesterDepartments,
)
from app.DB.semesters import get_semester_by_hijri_code
from app.main import app

# A Wednesday in Riyadh, inside the seeded Summer 2026 (475) the suite pins as current.
FROZEN_NOW = datetime(2026, 7, 15, 9, 0, 0)  # 12:00 in Riyadh


class Pipeline:
    """Builds the rows a pipeline test needs and signs in as whoever it created."""

    def __init__(self, session, client, monkeypatch):
        self.session = session
        self.client = client
        self.monkeypatch = monkeypatch
        self.semester_id = get_semester_by_hijri_code(session, 475).id
        self._counter = 0
        self.now = FROZEN_NOW
        self.freeze(FROZEN_NOW)

    def freeze(self, when: datetime) -> None:
        from app.services import event_pipeline_clock

        self.now = when
        self.monkeypatch.setattr(event_pipeline_clock, "now", lambda: self.now)

    def department(self, name: str) -> Departments:
        department = Departments(name=name, ar_name=name, type=DepartmentsType.PRACTICAL)
        self.session.add(department)
        self.session.flush()
        self.session.add(SemesterDepartments(semester_id=self.semester_id, department_id=department.id))
        self.session.flush()
        return department

    def person(self, name: str = "Person", email: str | None = None) -> Members:
        self._counter += 1
        member = Members(
            name=f"{name} {self._counter}",
            email=email or f"pipeline{self._counter}@example.com",
            uni_id=f"77{self._counter:07d}",
            clerk_user_id=f"clerk_pipeline_{self._counter}",
            gender=MembersGender.MALE,
        )
        self.session.add(member)
        self.session.flush()
        return member

    def join(self, member: Members, department: Departments, *roles: str) -> Members:
        """Put ``member`` in ``department`` this semester, always as a member plus any officer roles."""
        for key in ("member", *roles):
            self.session.add(
                ClubMemberships(
                    semester_id=self.semester_id,
                    department_id=department.id,
                    member_id=member.id,
                    role_id=get_role_by_key(self.session, key).id,
                    created_by="test",
                )
            )
        self.session.flush()
        return member

    def officer(self, department: Departments, role: str = "leader", name: str = "Leader") -> Members:
        return self.join(self.person(name), department, role)

    def teams(self, design: Departments, logistics: Departments, media: Departments) -> None:
        for row in self.session.scalars(select(PipelineTeams)).all():
            self.session.delete(row)
        self.session.flush()
        for team, department in (
            (PipelineTeam.DESIGN, design),
            (PipelineTeam.LOGISTICS, logistics),
            (PipelineTeam.MEDIA, media),
        ):
            self.session.add(PipelineTeams(team=team, department_id=department.id))
        self.session.flush()

    def sign_in(self, member: Members | None, super_admin: bool = False) -> None:
        """Sign in as ``member`` through the real guards; only JWT verification is replaced."""
        metadata = {"is_super_admin": True} if super_admin else {}
        subject = member.clerk_user_id if member else "clerk_nobody"
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="test-token", decoded={"sub": subject, "metadata": metadata}
        )
        app.dependency_overrides[config.CLERK_GUARD] = lambda: credentials
        self.session.commit()


@pytest.fixture
def pipeline(db_session, client, monkeypatch):
    helper = Pipeline(db_session, client, monkeypatch)
    yield helper
    app.dependency_overrides.pop(config.CLERK_GUARD, None)


COMPLETE_DETAILS = {
    "title": "Intro to ML",
    "description": "Hands-on machine learning",
    "event_type": "workshop",
    "presenter_name": "Dr. Noura",
    "presenter_email": "noura@example.com",
    "day_modes": {"2026-07-20": "on_site", "2026-07-21": "online"},
    "daily_start_time": "10:00",
    "daily_end_time": "12:00",
    "is_official": True,
    "location_scope": "inside",
    "audience": "mixed",
    "registration": "open",
}
COMPLETE_DESIGN = {"design_type": "poster", "idea": "A robot", "content_status": "final", "content": "Join us"}
COMPLETE_LOGISTICS = {"meet_link_by_logistics": True, "venue": "التيك فالي (60)", "buses_needed": False}


def book_complete(pipeline, department, start="2026-07-20", end="2026-07-21") -> int:
    """Book a request as the signed-in person and fill in everything submit needs. Returns its id."""
    response = pipeline.client.post(
        "/pipeline/requests", json={"department_id": department.id, "start_date": start, "end_date": end}
    )
    assert response.status_code == 201, response.text
    request_id = response.json()["id"]
    url = f"/pipeline/requests/{request_id}"
    assert pipeline.client.put(f"{url}/details", json=COMPLETE_DETAILS).status_code == 200
    assert pipeline.client.put(f"{url}/briefs/design", json={"brief": COMPLETE_DESIGN}).status_code == 200
    assert pipeline.client.put(f"{url}/briefs/logistics", json={"brief": COMPLETE_LOGISTICS}).status_code == 200
    return request_id


def submit(pipeline, request_id: int):
    return pipeline.client.post(f"/pipeline/requests/{request_id}/submit")
