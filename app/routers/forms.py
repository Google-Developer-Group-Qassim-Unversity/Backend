from fastapi import APIRouter, Depends, HTTPException, status
from app.routers.models import Form_model, NotFoundResponse
from app.DB import forms as form_queries
from app.DB.main import SessionLocal
from app.routers.logging import LogFile, write_log_exception, write_log_traceback, write_log_title, write_log_json_to
from app.helpers import admin_guard

router = APIRouter()


@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Form_model])
def get_all_forms():
    with SessionLocal() as session:
        forms = form_queries.get_forms(session)
    return forms


@router.get(
    "/{form_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Form_model,
    responses={404: {"model": NotFoundResponse, "description": "Form not found"}},
)
def get_form_by_id(form_id: int):
    with SessionLocal() as session:
        form = form_queries.get_form_by_id(session, form_id)
        if not form:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Form with id {form_id} not found")
    return form


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=Form_model)
def create_form(form: Form_model, credentials=Depends(admin_guard)):
    with LogFile("create form") as log, SessionLocal() as session:
        try:
            write_log_title("Creating New Form")
            new_form = form_queries.create_form(session, form)
            session.commit()
            return new_form
        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while creating the form"
            )
        finally:
            write_log_json_to(log.file, form.model_dump())


@router.put(
    "/{form_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Form_model,
    responses={
        404: {"model": NotFoundResponse, "description": "Form not found"},
        409: {"model": NotFoundResponse, "description": "Form with event_id already exists"},
    },
)
def update_form(form_id: int, form: Form_model, credentials=Depends(admin_guard)):
    with LogFile("update form") as log, SessionLocal() as session:
        try:
            write_log_title(f"Updating Form {form_id}")
            updated_form = form_queries.update_form(session, form_id, form)
            if updated_form is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Form with id {form_id} not found")
            if updated_form == -1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=f"Form with event_id {form.event_id} already exists"
                )
            session.commit()
            return updated_form
        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while updating the form"
            )
        finally:
            write_log_json_to(log.file, form.model_dump())


@router.delete(
    "/{form_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Form_model,
    responses={404: {"model": NotFoundResponse, "description": "Form not found"}},
)
def delete_form(form_id: int, credentials=Depends(admin_guard)):
    with LogFile("delete form"), SessionLocal() as session:
        try:
            write_log_title(f"Deleting Form {form_id}")
            deleted_form = form_queries.delete_form(session, form_id)
            if deleted_form is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Form with id {form_id} not found")
            session.commit()
            return deleted_form
        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while deleting the form"
            )
