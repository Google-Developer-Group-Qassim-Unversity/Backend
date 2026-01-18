import json
from typing import Literal
from fastapi import APIRouter, Depends, Request, status, HTTPException, BackgroundTasks
from app.DB.main import SessionLocal
from app.DB import submissions as submission_queries, members as member_queries, forms as form_queries
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.helpers import admin_guard, get_uni_id_from_credentials
from app.config import config
from app.routers.logging import create_log_file, write_log, write_log_exception, write_log_json, write_log_title, write_log_traceback
from app.routers.models import submission_exists_model
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request as GoogleRequest
import os

router = APIRouter()


def get_google_credentials(refresh_token: str):
    """Get Google credentials from refresh token"""
    credentials = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET
    )
    
    # Refresh the token if needed
    if not credentials.valid:
        credentials.refresh(GoogleRequest())
    
    return credentials

@router.get("/test-google-forms/{google_form_id}", status_code=status.HTTP_200_OK)
def fetch_form_responses(google_form_id: str):
    log_file = create_log_file(f"google forms fetch {google_form_id}")
    """Fetch all responses from a Google Form"""
    try:
        write_log(log_file, f"\n=== Fetching responses for form: {google_form_id} ===")
        
        # Get form details from database to retrieve refresh token
        with SessionLocal() as session:
            form = form_queries.get_from_by_google_form_id(session, google_form_id)
            
            if not form:
                write_log(log_file, f"ERROR: Form not found in database for google_form_id: {google_form_id}")
                return
            
            if not form.google_refresh_token:
                write_log(log_file, f"ERROR: No refresh token available for form: {google_form_id}")
                return
            
            write_log(log_file, f"Found form in database with ID: {form.id}")
            
            # Get Google credentials
            credentials = get_google_credentials(form.google_refresh_token)
            write_log(log_file, "Successfully authenticated with Google")
            
            # Build the Forms API service
            service = build('forms', 'v1', credentials=credentials)
            
            # Fetch the form responses
            result = service.forms().responses().list(formId=google_form_id).execute()
            
            responses = result.get('responses', [])
            write_log(log_file, f"\nTotal responses found: {len(responses)}")
            
            # Print all responses
            for idx, response in enumerate(responses, 1):
                write_log(log_file, f"\n--- Response #{idx} ---")
                write_log_json(log_file, response)
            
            write_log(log_file, "\n=== Finished fetching responses ===")
            
    except Exception as e:
        write_log_exception(log_file, e)
        write_log_traceback(log_file)


@router.post("/{form_id:int}", status_code=status.HTTP_200_OK)
def register_for_event(form_id: int, submission_type: Literal['none', 'partial'], credentials: HTTPAuthorizationCredentials = Depends(admin_guard)):
    with SessionLocal() as session:
        try:
            uni_id = get_uni_id_from_credentials(credentials)
            member_id = member_queries.get_member_by_uni_id(session, uni_id).id
            new_submission = submission_queries.create_submission(session, form_id, submission_type, member_id)
            if not new_submission:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Submission already exists")
            session.commit()
            return new_submission
        except Exception as e:
            session.rollback()
            raise
        
@router.get("/{form_id:int}", status_code=status.HTTP_200_OK, response_model=submission_exists_model)
def check_submission_exists(form_id: int, credentials: HTTPAuthorizationCredentials = Depends(admin_guard)):
    with SessionLocal() as session:
        try:
            uni_id = get_uni_id_from_credentials(credentials)
            member_id = member_queries.get_member_by_uni_id(session, uni_id).id
            submission = submission_queries.get_submission_by_form_and_member(session, form_id, member_id)
            if submission is None:
                return {'submission_status': False}
            submission_type = submission.submission_type 
            if submission_type == 'partial':
                return {'submission_status': 'partial', "submission_timestamp": submission.submitted_at}
            return {'submission_status': True, "submission_timestamp": submission.submitted_at}
        except Exception as e:
            raise

@router.post("/google/webhook", status_code=status.HTTP_200_OK)
async def google_forms_webhook(request: Request, background_tasks: BackgroundTasks):
    log_file = create_log_file("google forms webhook")
    try:
        write_log_title(log_file, "Google Forms Webhook Notification")
        
        # Parse the incoming request body
        body = await request.json()
        
        # Validate it's a Pub/Sub message
        if "message" not in body:
            write_log_json(log_file, {"status": "ignored", "reason": "not_pubsub_message", "body": body})
            return {"status": "ignored", "reason": "not_pubsub_message"}
        
        write_log(log_file, f"Received Pub/Sub message: {body}")
        
        message = body["message"]
        
        # Validate message contains attributes field
        if "attributes" not in message:
            write_log_json(log_file, {"status": "ignored", "reason": "missing_attributes_field", "body": body})
            return {"status": "ignored", "reason": "missing_attributes_field"}
        
        attributes = message["attributes"]
        
        # Extract form information from attributes
        form_id = attributes.get("formId")
        watch_id = attributes.get("watchId")
        event_type = attributes.get("eventType")
        message_id = message.get("messageId") or message.get("message_id")
        publish_time = message.get("publishTime") or message.get("publish_time")
        subscription = body.get("subscription")
        
        if not form_id:
            write_log_json(log_file, {"status": "ignored", "reason": "missing_form_id"})
            return {"status": "ignored", "reason": "missing_form_id"}
        
        # Log the notification
        write_log_json(log_file, {
            "status": "received",
            "form_id": form_id,
            "watch_id": watch_id,
            "event_type": event_type,
            "message_id": message_id,
            "publish_time": publish_time,
            "subscription": subscription
        })
        
        # Fetch form responses in the background
        background_tasks.add_task(fetch_form_responses, form_id, log_file)
        write_log(log_file, f"Background task scheduled to fetch responses for form: {form_id}")
        
        return {
            "status": "received",
            "form_id": form_id,
            "event_type": event_type,
            "message_id": message_id
        }
        
    except json.JSONDecodeError as e:
        write_log_exception(log_file, e)
        write_log_traceback(log_file)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON in message data")
    except KeyError as e:
        write_log_exception(log_file, e)
        write_log_traceback(log_file)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Missing required field: {str(e)}")
    except Exception as e:
        write_log_exception(log_file, e)
        write_log_traceback(log_file)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while processing the webhook")
