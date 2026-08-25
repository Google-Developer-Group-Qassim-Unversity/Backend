"""Admin CRUD for semesters.

Semesters used to be a hardcoded dict in ``app/config.py``; they now live in
the DB so an admin can add one, retime it, or switch which one is the default
without a redeploy. Every mutation best-effort resets the leaderboard app's
cache so the change is visible there immediately too.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status

from app.DB import semesters as semesters_queries
from app.DB.main import SessionLocal
from app.exceptions import SemesterNotFound
from app.helpers import admin_guard, super_admin_guard
from app.leaderboard_cache import reset_leaderboard_cache
from app.routers.logging import LogFile, write_log, write_log_exception, write_log_title
from app.routers.models import BaseClassModel

router = APIRouter()


# ============ models ============


class Semester_model(BaseClassModel):
    id: int
    name: str | None = None
    start_date: date
    end_date: date
    is_current: bool
    is_public: bool


class CreateSemester_model(BaseClassModel):
    id: int
    name: str | None = None
    start_date: date
    end_date: date
    is_public: bool = True
    is_current: bool = False


class UpdateSemester_model(BaseClassModel):
    name: str | None = None
    start_date: date
    end_date: date
    is_public: bool = True


# ============ helpers ============


def _validate_dates(start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="End date must be on or after the start date"
        )


def _reset_cache_best_effort() -> None:
    """The leaderboard app caches semester-scoped data; nudge it after a change."""
    try:
        reset_leaderboard_cache()
        write_log("Leaderboard cache reset triggered after semester change")
    except Exception as cache_err:
        write_log_exception(cache_err)


# ============ routes ============


@router.get("", status_code=status.HTTP_200_OK, response_model=list[Semester_model])
def get_all_semesters(credentials=Depends(admin_guard)):
    with SessionLocal() as session:
        return semesters_queries.get_semesters(session)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Semester_model)
def create_semester(payload: CreateSemester_model, credentials=Depends(super_admin_guard)):
    _validate_dates(payload.start_date, payload.end_date)
    with LogFile("create semester"), SessionLocal() as session:
        write_log_title(f"Creating semester [{payload.id}]")
        if semesters_queries.get_semester_by_id(session, payload.id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Semester {payload.id} already exists")

        semester = semesters_queries.create_semester(
            session,
            semester_id=payload.id,
            name=payload.name,
            start_date=payload.start_date,
            end_date=payload.end_date,
            is_public=payload.is_public,
            is_current=payload.is_current,
        )
        session.commit()
        session.refresh(semester)
        write_log(f"Created semester [{semester.id}] {semester.start_date} → {semester.end_date}")
        result = Semester_model.model_validate(semester)
        _reset_cache_best_effort()

    return result


@router.put("/{semester_id:int}", status_code=status.HTTP_200_OK, response_model=Semester_model)
def update_semester(semester_id: int, payload: UpdateSemester_model, credentials=Depends(super_admin_guard)):
    _validate_dates(payload.start_date, payload.end_date)
    with LogFile("update semester"), SessionLocal() as session:
        write_log_title(f"Updating semester [{semester_id}]")
        semester = semesters_queries.get_semester_by_id(session, semester_id)
        if semester is None:
            raise SemesterNotFound(semester_id)

        semesters_queries.update_semester(
            session,
            semester,
            name=payload.name,
            start_date=payload.start_date,
            end_date=payload.end_date,
            is_public=payload.is_public,
        )
        session.commit()
        session.refresh(semester)
        write_log(f"Updated semester [{semester_id}] {semester.start_date} → {semester.end_date}")
        result = Semester_model.model_validate(semester)
        _reset_cache_best_effort()

    return result


@router.put("/{semester_id:int}/current", status_code=status.HTTP_200_OK, response_model=Semester_model)
def set_current_semester(semester_id: int, credentials=Depends(super_admin_guard)):
    """Make this the default semester for requests that don't name one."""
    with LogFile("set current semester"), SessionLocal() as session:
        write_log_title(f"Setting semester [{semester_id}] as current")
        semester = semesters_queries.get_semester_by_id(session, semester_id)
        if semester is None:
            raise SemesterNotFound(semester_id)

        semesters_queries.set_current_semester(session, semester_id)
        session.commit()
        session.refresh(semester)
        result = Semester_model.model_validate(semester)
        _reset_cache_best_effort()

    return result


@router.delete("/{semester_id:int}", status_code=status.HTTP_200_OK)
def delete_semester(semester_id: int, credentials=Depends(super_admin_guard)):
    with LogFile("delete semester"), SessionLocal() as session:
        write_log_title(f"Deleting semester [{semester_id}]")
        semester = semesters_queries.get_semester_by_id(session, semester_id)
        if semester is None:
            raise SemesterNotFound(semester_id)
        if semester.is_current:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the current semester. Set another semester as current first.",
            )

        semesters_queries.delete_semester(session, semester)
        session.commit()
        write_log(f"Deleted semester [{semester_id}]")
        _reset_cache_best_effort()

    return {"detail": "Semester deleted successfully"}
