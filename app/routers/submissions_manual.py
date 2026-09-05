import logging
from fastapi import Depends, APIRouter, Query, status

from app.helpers import admin_guard
from app.services.form_responses import FormResponsesClient
from app.services.form_sync import sync_form_submissions, sync_manual_form_submissions
from typing import Annotated

from app.routers.responses import ManualSyncResponse


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/submissions_manual", tags=["Submissions Manual"])


@router.post(
    "/google/{google_form_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=ManualSyncResponse,
)
def manual_create_google_submissions(
    google_form_id: str, responses_client: FormResponsesClient, limit: Annotated[int, Query(ge=1, le=2000)] = 50
):
    """Backfill submissions from a form's Google responses.

    Processes only the first `limit` responses as returned by the Google API.
    """
    return sync_manual_form_submissions(google_form_id, limit, responses_client)


@router.post("/google/run/{google_form_id}", status_code=status.HTTP_200_OK, dependencies=[Depends(admin_guard)])
def manual_run_google_form_submissions(google_form_id: str, responses_client: FormResponsesClient):
    return sync_form_submissions(google_form_id, responses_client=responses_client)
