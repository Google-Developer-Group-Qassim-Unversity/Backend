"""The one Google credential every Drive/Forms *write* call in the backend uses.

A single club-owned account, not one per admin or per form - see
docs/GOOGLE_FORMS.md. Reading a form's schema/responses goes through the
testable seam in app/services/form_responses.py instead; this module exists
for the one-off write operations that seam doesn't cover - copying the
template, sharing it, registering/deleting a watch, and publishing.
"""

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import config


def get_google_credentials() -> Credentials:
    """Build credentials for the one club-owned Google account every form is created and read under.

    See docs/GOOGLE_FORMS.md - there is no per-admin/per-event token anymore.
    """
    credentials = Credentials(
        None,
        refresh_token=config.GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
    )
    if not credentials.valid:
        credentials.refresh(GoogleRequest())
    return credentials


def set_form_publish_state(google_form_id: str, is_published: bool) -> None:
    """Publish or unpublish a Google Form via the Forms API.

    Copying the template with Drive's files.copy does NOT carry over the
    "published, accepting responses" state - confirmed the hard way: a freshly
    copied form shows members an "unpublished form" page until someone
    publishes it, previously only possible by opening the form in Google
    Forms and doing it by hand. This is what wires that into the event's own
    publish/unpublish action instead. isAcceptingResponses is kept equal to
    isPublished; this app has no use case for "published but closed".
    """
    credentials = get_google_credentials()
    service = build("forms", "v1", credentials=credentials)
    service.forms().setPublishSettings(
        formId=google_form_id,
        body={"publishSettings": {"publishState": {"isPublished": is_published, "isAcceptingResponses": is_published}}},
    ).execute()
