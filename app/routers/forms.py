from fastapi import APIRouter, Depends, status
from app.routers.models import Form_model, NotFoundResponse
from app.DB import forms as form_queries

from app.routers.logging import LogFile, write_log_exception, write_log_traceback, write_log_title, write_log_json_to
from app.helpers import admin_guard
from app.exceptions import FormNotFoundById
from app.dependencies import DB

router = APIRouter(prefix="/forms", tags=["Forms"])


@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Form_model])
def get_all_forms(session: DB):
    forms = form_queries.get_forms(session)
    return forms


@router.get(
    "/{form_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Form_model,
    responses={404: {"model": NotFoundResponse, "description": "Form not found"}},
)
def get_form_by_id(form_id: int, session: DB):
    form = form_queries.get_form_by_id(session, form_id)
    if not form:
        raise FormNotFoundById(form_id)
    return form


@router.put(
    "/{form_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Form_model,
    responses={
        404: {"model": NotFoundResponse, "description": "Form not found"},
        409: {"model": NotFoundResponse, "description": "Form with event_id already exists"},
    },
    dependencies=[Depends(admin_guard)],
)
def update_form(form_id: int, form: Form_model, session: DB):
    with LogFile("update form") as log:
        try:
            write_log_title(f"Updating Form {form_id}")
            updated_form = form_queries.update_form(session, form_id, form)
            session.commit()
            return updated_form
        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            raise
        finally:
            write_log_json_to(log.file, form.model_dump())
