import logging
from fastapi import APIRouter, Depends, status
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from app.routers.models import Form_model, NotFoundResponse, AttachFormRequest
from app.routers.submissions import get_google_credentials, fetch_schema
from app.DB import forms as form_queries
from app.DB.schema import FormType

from app.helpers import admin_guard
from app.exceptions import FormNotFoundById, FormNotAttached
from app.dependencies import DB
from app.config import config

logger = logging.getLogger(__name__)


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
    try:
        logger.info(f"Updating Form {form_id}")
        updated_form = form_queries.update_form(session, form_id, form)
        session.commit()
        return updated_form
    except Exception as e:
        session.rollback()
        logger.exception(e)
        raise
    finally:
        logger.debug("request body: %s", form.model_dump())


@router.post(
    "/{event_id:int}/attach",
    status_code=status.HTTP_200_OK,
    response_model=Form_model,
    responses={404: {"model": NotFoundResponse, "description": "Event or form not found"}},
    dependencies=[Depends(admin_guard)],
)
def attach_form(event_id: int, body: AttachFormRequest, session: DB):
    """Attach a Google Form to an event and invite an admin to edit it.

    Idempotent: if the event already has a form (this is a "request access
    for a different email" call), only the sharing step runs - the form is
    never re-copied. See docs/GOOGLE_FORMS.md.
    """
    form = form_queries.get_form_by_event_id(session, event_id)
    credentials = get_google_credentials()
    drive = build("drive", "v3", credentials=credentials)

    if form.google_form_id:
        google_form_id = form.google_form_id
        google_watch_id = form.google_watch_id
        google_responders_url = form.google_responders_url
    else:
        copy_response = drive.files().copy(fileId=config.TEMPLATE_FORM_FILE_ID, fields="id").execute()
        google_form_id = copy_response["id"]

        forms_service = build("forms", "v1", credentials=credentials)
        watch_response = (
            forms_service.forms()
            .watches()
            .create(
                formId=google_form_id,
                body={
                    "watch": {
                        "target": {"topic": {"topicName": config.GOOGLE_FORMS_TOPIC_NAME}},
                        "eventType": "RESPONSES",
                    }
                },
            )
            .execute()
        )
        google_watch_id = watch_response["id"]

        form_details = forms_service.forms().get(formId=google_form_id).execute()
        google_responders_url = form_details.get("responderUri")

    drive.permissions().create(
        fileId=google_form_id,
        sendNotificationEmail=True,
        body={"role": "writer", "type": "user", "emailAddress": body.admin_google_email},
    ).execute()

    updated_form = form_queries.update_form(
        session,
        form.id,
        Form_model(
            event_id=form.event_id,
            form_type=FormType.GOOGLE,
            google_form_id=google_form_id,
            google_watch_id=google_watch_id,
            google_responders_url=google_responders_url,
            admin_google_email=body.admin_google_email,
        ),
    )
    session.commit()
    logger.info(f"Attached Google Form {google_form_id} to event {event_id}, shared with {body.admin_google_email}")
    return updated_form


@router.post(
    "/{event_id:int}/unattach",
    status_code=status.HTTP_200_OK,
    response_model=Form_model,
    responses={404: {"model": NotFoundResponse, "description": "Event or form not found"}},
    dependencies=[Depends(admin_guard)],
)
def unattach_form(event_id: int, session: DB):
    """Revoke the invited admin's access, delete the Forms watch, and reset the form row.

    The form itself stays in the club's Drive - only access to it changes.
    """
    form = form_queries.get_form_by_event_id(session, event_id)

    if form.google_form_id:
        credentials = get_google_credentials()

        if form.google_watch_id:
            try:
                forms_service = build("forms", "v1", credentials=credentials)
                forms_service.forms().watches().delete(
                    formId=form.google_form_id, watchId=form.google_watch_id
                ).execute()
            except HttpError:
                # Watches expire on their own after 7 days, so a 404 here just means
                # it already lapsed - not a reason to abort the rest of unattach.
                logger.exception(f"Failed to delete watch {form.google_watch_id} for form {form.google_form_id}")

        if form.admin_google_email:
            try:
                drive = build("drive", "v3", credentials=credentials)
                permissions = (
                    drive.permissions()
                    .list(fileId=form.google_form_id, fields="permissions(id,emailAddress)")
                    .execute()
                    .get("permissions", [])
                )
                permission_id = next(
                    (
                        permission["id"]
                        for permission in permissions
                        if permission.get("emailAddress") == form.admin_google_email
                    ),
                    None,
                )
                if permission_id:
                    drive.permissions().delete(fileId=form.google_form_id, permissionId=permission_id).execute()
            except HttpError:
                logger.exception(f"Failed to revoke access for {form.admin_google_email} on form {form.google_form_id}")

    updated_form = form_queries.update_form(
        session,
        form.id,
        Form_model(
            event_id=form.event_id,
            form_type=FormType.REGISTRATION,
            google_form_id=None,
            google_watch_id=None,
            google_responders_url=None,
            admin_google_email=None,
        ),
    )
    session.commit()
    logger.info(f"Unattached Google Form from event {event_id}")
    return updated_form


@router.get(
    "/{form_id:int}/schema",
    status_code=status.HTTP_200_OK,
    response_model=dict,
    responses={404: {"model": NotFoundResponse, "description": "Form not found"}},
    dependencies=[Depends(admin_guard)],
)
def get_form_schema(form_id: int, session: DB):
    form = form_queries.get_form_by_id(session, form_id)
    if not form:
        raise FormNotFoundById(form_id)
    if not form.google_form_id:
        raise FormNotAttached(form_id)
    return fetch_schema(form.google_form_id)
