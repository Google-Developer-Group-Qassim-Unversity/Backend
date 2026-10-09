import logging
from fastapi import APIRouter, Depends, status
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from app.routers.models import Form_model, NotFoundResponse, AttachFormRequest
from app.routers.responses import WatchRenewalResponse
from app.services.form_watches import renew_form_watches
from app.services.google_client import get_google_credentials
from app.services.form_responses import FormAccess, FormResponsesClient
from app.DB import forms as form_queries
from app.DB.schema import EventHistoryAction, Forms, FormType
from app.helpers import CurrentMember
from app.services import event_history


from app.exceptions import FormNotFoundById, FormNotAttached
from app.dependencies import DB
from app.config import config
from app.services.permissions.catalogue import Perm
from app.services.permissions.guards import Require
from app.services.permissions.departments import event_departments, form_departments

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


def _form_snapshot(form: Forms) -> dict:
    return {
        "form_type": form.form_type.value if form.form_type else None,
        "google_form_id": form.google_form_id,
        "google_responders_url": form.google_responders_url,
        "admin_google_email": form.admin_google_email,
    }


@router.put(
    "/{form_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Form_model,
    responses={
        404: {"model": NotFoundResponse, "description": "Form not found"},
        409: {"model": NotFoundResponse, "description": "Form with event_id already exists"},
    },
    dependencies=[Depends(Require(Perm.FORMS_MANAGE, form_departments))],
)
def update_form(form_id: int, form: Form_model, session: DB, actor: CurrentMember):
    try:
        logger.info(f"Updating Form {form_id}")
        existing = form_queries.get_form_by_id(session, form_id)
        before = _form_snapshot(existing) if existing else {}
        updated_form = form_queries.update_form(session, form_id, form)
        changed = event_history.changes(before, _form_snapshot(updated_form))
        if changed:
            event_history.record(session, updated_form.event_id, EventHistoryAction.FORM_UPDATED, actor, changed)
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
    dependencies=[Depends(Require(Perm.FORMS_MANAGE, event_departments))],
)
def attach_form(event_id: int, body: AttachFormRequest, session: DB, actor: CurrentMember):
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
        event_name = form.event.name
        copy_response = (
            drive.files().copy(fileId=config.TEMPLATE_FORM_FILE_ID, fields="id", body={"name": event_name}).execute()
        )
        google_form_id = copy_response["id"]

        forms_service = build("forms", "v1", credentials=credentials)

        # files.copy only renames the Drive file - the form's own title (what
        # respondents see) is a separate property, only settable through the
        # Forms API itself.
        forms_service.forms().batchUpdate(
            formId=google_form_id,
            body={"requests": [{"updateFormInfo": {"info": {"title": event_name}, "updateMask": "title"}}]},
        ).execute()

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
    form_queries.grant_form_access(session, form.id, body.admin_google_email)

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
    event_history.record(
        session,
        event_id,
        EventHistoryAction.FORM_ATTACHED,
        actor,
        {"google_form_id": google_form_id, "admin_google_email": body.admin_google_email},
    )
    session.commit()
    logger.info(f"Attached Google Form {google_form_id} to event {event_id}, shared with {body.admin_google_email}")
    return updated_form


@router.post(
    "/{event_id:int}/unattach",
    status_code=status.HTTP_200_OK,
    response_model=Form_model,
    responses={404: {"model": NotFoundResponse, "description": "Event or form not found"}},
    dependencies=[Depends(Require(Perm.FORMS_MANAGE, event_departments))],
)
def unattach_form(event_id: int, session: DB, actor: CurrentMember):
    """Revoke every admin's access, delete the Forms watch, and reset the form row.

    The form itself stays in the club's Drive - only access to it changes.
    """
    form = form_queries.get_form_by_event_id(session, event_id)
    granted_emails = form_queries.get_form_access_grants(session, form.id)

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

        if granted_emails:
            try:
                drive = build("drive", "v3", credentials=credentials)
                permissions = (
                    drive.permissions()
                    .list(fileId=form.google_form_id, fields="permissions(id,emailAddress)")
                    .execute()
                    .get("permissions", [])
                )
                permission_ids_by_email = {
                    permission.get("emailAddress"): permission["id"]
                    for permission in permissions
                    if permission.get("emailAddress")
                }
                for email in granted_emails:
                    permission_id = permission_ids_by_email.get(email)
                    if permission_id:
                        drive.permissions().delete(fileId=form.google_form_id, permissionId=permission_id).execute()
            except HttpError:
                logger.exception(f"Failed to revoke access for {granted_emails} on form {form.google_form_id}")

    form_queries.clear_form_access_grants(session, form.id)

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
    event_history.record(
        session,
        event_id,
        EventHistoryAction.FORM_DETACHED,
        actor,
        {"google_form_id": form.google_form_id, "revoked": list(granted_emails)},
    )
    session.commit()
    logger.info(f"Unattached Google Form from event {event_id}, revoked {len(granted_emails)} admin(s)")
    return updated_form


@router.get(
    "/{form_id:int}/schema",
    status_code=status.HTTP_200_OK,
    response_model=dict,
    responses={404: {"model": NotFoundResponse, "description": "Form not found"}},
    dependencies=[Depends(Require(Perm.SUBMISSIONS_REVIEW, form_departments))],
)
def get_form_schema(form_id: int, session: DB, responses_client: FormResponsesClient):
    form = form_queries.get_form_by_id(session, form_id)
    if not form:
        raise FormNotFoundById(form_id)
    if not form.google_form_id:
        raise FormNotAttached(form_id)
    return responses_client.get_schema(FormAccess(google_form_id=form.google_form_id))


@router.post(
    "/watches/renew",
    status_code=status.HTTP_200_OK,
    response_model=WatchRenewalResponse,
    dependencies=[Depends(Require(Perm.FORMS_ADMIN))],
    description=(
        "Renew every registered Google Forms watch for another seven days, recreating any that "
        "have already expired. Google expires watches seven days after creation or last renewal, "
        "and an expired watch stops sending notifications silently - a form attached more than a "
        "week before its event simply stops syncing. Intended to run daily; "
        "scripts/renew_form_watches.py is the same sweep for cron."
    ),
)
def renew_watches(session: DB):
    """Manual trigger for the sweep that scripts/renew_form_watches.py runs on a timer.

    Synchronous rather than a BackgroundTask: the caller wants the outcome,
    there are only as many API calls as there are attached forms, and a
    background failure here would land in the same "response already started"
    trap that hid the credential expiry on the webhook path.
    """
    return renew_form_watches(session)
