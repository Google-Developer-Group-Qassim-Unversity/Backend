"""Opt-in Chromium → real HTTP routes → isolated MySQL integration check.

Run with RUN_CLUB_BROWSER=1 after installing Frontend dependencies and Chromium.
JWT verification is replaced only in this test process; real permission guards,
request schemas, services, transactions and queries run unchanged.
"""

from datetime import date
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from threading import Thread
import time
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
import uvicorn

from app.config import config
from app.DB.schema import (
    ClubMembershipAction,
    ClubMembershipChanges,
    ClubMemberships,
    Departments,
    DepartmentsType,
    Members,
    Role,
    SemesterDepartments,
    Semesters,
    SemesterTerm,
)
from app.dependencies import get_db
from app.main import app
from app.routers import club_structure
from tests.factories import make_member


@pytest.mark.skipif(os.environ.get("RUN_CLUB_BROWSER") != "1", reason="Set RUN_CLUB_BROWSER=1 for Chromium integration")
def test_browser_manages_club_through_real_api(engine, seed_core_data, monkeypatch, tmp_path):
    frontend = Path(__file__).resolve().parents[2] / "Frontend"
    prefix = f"Browser-{uuid4().hex[:10]}"
    with Session(engine) as session:
        current = session.scalar(select(Semesters).where(Semesters.hijri_code == 475))
        # An empty semester far in the future, never current, for "copy structure".
        future = Semesters(
            term=SemesterTerm.SUMMER,
            hijri_year=1460,
            academic_year_start=2038,
            start_date=date(2039, 6, 1),
            end_date=date(2039, 8, 1),
        )
        board = Departments(
            name=f"{prefix} Board", ar_name="مجلس الإدارة", type=DepartmentsType.ADMINISTRATIVE, show_in_leaderboard=0
        )
        people = [
            Members(**make_member(name=f"Browser {name}", email=f"{prefix}-{name}@example.com", uni_id=None))
            for name in ["Alice", "Bob", "Carol", "Dana"]
        ]
        session.add_all([future, board, *people])
        session.flush()
        session.add(SemesterDepartments(semester_id=current.id, department_id=board.id))
        refs = {
            "prefix": prefix,
            "board": board.id,
            "semester": {"id": current.id, "name": current.name},
            "future": {"id": future.id, "name": future.name},
            "members": [{"id": p.id, "name": p.name} for p in people],
        }
        future_id = future.id
        member_ids = [p.id for p in people]
        before_members = session.scalar(select(func.count()).select_from(Members))
        before_roles = session.scalar(select(func.count()).select_from(Role))
        session.commit()

    def database():
        with Session(engine) as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    def identity(request: Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        roles = {
            "browser-super_admin": {"is_super_admin": True},
            "browser-admin": {"is_admin": True},
            "browser-admin_points": {"is_admin_points": True},
            "browser-member": {},
        }
        if token not in roles:
            raise HTTPException(status_code=401, detail="Missing test identity")
        return HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=token, decoded={"sub": token, "metadata": roles[token]}
        )

    monkeypatch.setitem(app.dependency_overrides, get_db, database)
    monkeypatch.setitem(app.dependency_overrides, config.CLERK_GUARD, identity)
    cache_reset = Mock(return_value={"revalidated": True})
    monkeypatch.setattr(club_structure, "reset_leaderboard_cache", cache_reset)
    listener = socket.socket()
    server = None
    thread = None
    try:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        # No production lifespan/outbound clients are needed by these routes.
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "Test API did not start"
        env = {
            **os.environ,
            "CLUB_TEST_API": f"http://127.0.0.1:{port}",
            "CLUB_TEST_REFS": json.dumps(refs),
            "CLUB_TEST_ARTIFACTS": str(tmp_path),
        }
        result = subprocess.run(
            [shutil.which("node") or "node", "tests/club-structure/run.mjs"],
            cwd=frontend,
            env=env,
            text=True,
            capture_output=True,
            timeout=240,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}\nScreenshots: {tmp_path}"
        print(result.stdout)
        assert cache_reset.call_count >= 5  # create, edit, archive/restore cycles
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Members)) == before_members
            assert session.scalar(select(func.count()).select_from(Role)) == before_roles
            changes = session.scalars(
                select(ClubMembershipChanges).where(ClubMembershipChanges.member_id.in_(member_ids))
            ).all()
            assert any(change.action == ClubMembershipAction.REMOVED for change in changes)
            assert {change.actor for change in changes} == {"browser-super_admin"}
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)
        listener.close()
        with Session(engine) as session:
            department_ids = select(Departments.id).where(Departments.name.startswith(prefix))
            for table in (ClubMembershipChanges, ClubMemberships):
                session.execute(
                    delete(table).where(
                        table.member_id.in_(member_ids)
                        | table.department_id.in_(department_ids)
                        | (table.semester_id == future_id)
                    )
                )
            session.execute(delete(SemesterDepartments).where(SemesterDepartments.department_id.in_(department_ids)))
            session.execute(delete(Semesters).where(Semesters.id == future_id))
            session.execute(delete(Departments).where(Departments.name.startswith(prefix)))
            session.execute(delete(Members).where(Members.id.in_(member_ids)))
            session.commit()
