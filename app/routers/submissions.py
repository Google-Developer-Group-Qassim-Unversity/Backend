import json
from typing import Literal
from fastapi import APIRouter, Depends, Request, status, HTTPException, BackgroundTasks
from app.DB.main import SessionLocal
from app.DB import submissions as submission_queries, members as member_queries, forms as form_queries
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.helpers import admin_guard, get_uni_id_from_credentials
from app.config import config
from app.routers.logging import create_log_file, write_log, write_log_exception, write_log_json, write_log_title, write_log_traceback
from app.routers.models import Submission_model, submission_exists_model, Member_model
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request as GoogleRequest
import os

router = APIRouter()



@router.post("/{form_id:int}", status_code=status.HTTP_200_OK)
def create_submission(form_id: int, submission_type: Literal['none', 'partial'], credentials: HTTPAuthorizationCredentials = Depends(admin_guard)):
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

@router.get("/{event_id:int}", status_code=status.HTTP_200_OK, response_model=list[Submission_model])
def get_submissions_by_event(event_id: int, credentials: HTTPAuthorizationCredentials = Depends(admin_guard)):
    with SessionLocal() as session:
        try:
            submissions_data = submission_queries.get_submissions_by_event_id(session, event_id)
            
            # Transform to Submission_model objects
            submissions = []
            for row in submissions_data:
                member = Member_model(
                    id=row.id,
                    name=row.name,
                    email=row.email,
                    phone_number=row.phone_number,
                    uni_id=row.uni_id,
                    gender=row.gender,
                    uni_level=row.uni_level,
                    uni_college=row.uni_college
                )
                
                submission = Submission_model(
                    member=member,
                    submission_id=row.submission_id,
                    submitted_at=row.submitted_at,
                    form_type=row.form_type,
                    submission_type=row.submission_type,
                    is_accepted=bool(row.is_accepted),
                    google_submission_value=row.google_submission_value,
                    event_id=row.event_id,
                    form_id=row.form_id,
                    googl_form_id=row.google_form_id
                )
                submissions.append(submission)
            
            return submissions
        except Exception as e:
            raise

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

def fetch_schema(google_form_id: str):
    """Fetch the form schema from Google Forms API"""
    with SessionLocal() as session:
        form = form_queries.get_form_by_google_form_id(session, google_form_id)
        
        if not form:
            raise ValueError(f"Form not found in database for google_form_id: {google_form_id}")
        
        if not form.google_refresh_token:
            raise ValueError(f"Form does not have a refresh token")
        
        # Get Google credentials
        credentials = get_google_credentials(form.google_refresh_token)
        
        # Build the Forms API service
        service = build('forms', 'v1', credentials=credentials)
        
        # Fetch the form schema from Google
        schema = service.forms().get(formId=google_form_id).execute()
        
        return schema


def get_uni_id_question_id(form_id: int) -> str:
    """
    Get the Google Forms question ID for the uni_id field by fetching the form schema from Google API.
    Looks for the question with title "الرقم الجامعي" (University Number).
    """
    with SessionLocal() as session:
        form = form_queries.get_form_by_id(session, form_id)
        
        if not form:
            raise ValueError(f"Form not found with id: {form_id}")
        
        # Fetch the form schema
        schema = fetch_schema(form.google_form_id)
        
        # Look through items for the question with title "الرقم الجامعي"
        items = schema.get('items', [])
        for item in items:
            title = item.get('title', '')
            if title == "الرقم الجامعي":
                question_item = item.get('questionItem')
                if question_item:
                    question = question_item.get('question')
                    if question:
                        question_id = question.get('questionId')
                        if question_id:
                            return question_id
        
        raise ValueError(f"Could not find question with title 'الرقم الجامعي' in form {form_id} schema")

def fetch_form_responses(google_form_id: str, log_file=None):
    """Fetch all responses from a Google Form and return them"""
    try:
        write_log_title(log_file, f"Fetching responses for form: {google_form_id}")
        
        # Get form details from database to retrieve refresh token
        with SessionLocal() as session:
            form = form_queries.get_form_by_google_form_id(session, google_form_id)
            
            if not form:
                write_log(log_file, f"ERROR: Form not found in database for google_form_id: {google_form_id}")
                return None
            
            if not form.google_refresh_token:
                write_log(log_file, f"ERROR: No refresh token available for form: {google_form_id}")
                return None
            
            write_log(log_file, f"Found form in database with ID: {form.id}")
            form_id = form.id
            
            # Get Google credentials
            credentials = get_google_credentials(form.google_refresh_token)
            write_log(log_file, "Successfully authenticated with Google")
            
            # Build the Forms API service
            service = build('forms', 'v1', credentials=credentials)
            
            # Fetch the form responses
            result = service.forms().responses().list(formId=google_form_id).execute()
            
            responses = result.get('responses', [])
            write_log(log_file, f"\nTotal responses found: {len(responses)}")
            
            # Log all responses
            for idx, response in enumerate(responses, 1):
                write_log(log_file, f"\n--- Response #{idx} ---")
                write_log_json(log_file, response)
            
            write_log(log_file, "\n=== Finished fetching responses ===")
            
            return {
                'form_id': form_id,
                'google_form_id': google_form_id,
                'responses': responses
            }
            
    except Exception as e:
        write_log_exception(log_file, e)
        write_log_traceback(log_file)
        return None


def sync_form_submissions(google_form_id: str, log_file):
    try:
        write_log_title(log_file, f"Syncing submissions google_form_id: {google_form_id}")
        
        # Fetch Google Form responses
        fetch_result = fetch_form_responses(google_form_id, log_file)
        
        if not fetch_result:
            write_log(log_file, "ERROR: Failed to fetch form responses")
            return
        
        form_id = fetch_result['form_id']
        google_responses = fetch_result['responses']
        
        write_log(log_file, f"Form ID: {form_id}")
        write_log(log_file, f"Google responses count: {len(google_responses)}")
        
        # Get partial submissions from database
        with SessionLocal() as session:
            partial_submissions = submission_queries.get_partial_submissions_by_form_id(session, form_id)
            write_log(log_file, f"Partial submissions count: {len(partial_submissions)}")
            
            if not partial_submissions:
                write_log(log_file, "No partial submissions to sync")
                return
            
            # Get the question ID for uni_id field
            try:
                uni_id_question_id = get_uni_id_question_id(form_id)
                write_log(log_file, f"Found uni_id question ID: {uni_id_question_id}")
            except Exception as e:
                write_log(log_file, f"ERROR: Failed to get uni_id question ID: {str(e)}")
                write_log_exception(log_file, e)
                return
            
            # Create a mapping of uni_id to partial submissions
            partial_by_uni_id = {}
            for submission in partial_submissions:
                uni_id = submission.uni_id
                partial_by_uni_id[uni_id] = submission
                write_log(log_file, f"Partial submission: ID={submission.id}, uni_id={uni_id}")
            
            # Match Google responses to partial submissions
            matched_count = 0
            unmatched_responses = []
            
            for response in google_responses:
                # Extract uni_id from response answers
                response_id = response.get('responseId')
                answers = response.get('answers', {})
                
                # Get the uni_id answer from the response
                uni_id_answer = answers.get(uni_id_question_id)
                
                if not uni_id_answer:
                    write_log(log_file, f"Response {response_id}: No uni_id answer found")
                    unmatched_responses.append(response_id)
                    continue
                
                # Extract the actual uni_id value from the answer
                # Google Forms stores answers in a specific format
                text_answers = uni_id_answer.get('textAnswers', {})
                answers_list = text_answers.get('answers', [])
                
                if not answers_list:
                    write_log(log_file, f"Response {response_id}: No text answers found")
                    unmatched_responses.append(response_id)
                    continue
                
                uni_id = answers_list[0].get('value', '').strip()
                
                if not uni_id:
                    write_log(log_file, f"Response {response_id}: Empty uni_id value")
                    unmatched_responses.append(response_id)
                    continue
                
                write_log(log_file, f"Response {response_id}: uni_id={uni_id}")
                
                # Check if this uni_id has a partial submission
                if uni_id in partial_by_uni_id:
                    partial_submission = partial_by_uni_id[uni_id]
                    
                    # Update submission with Google response data
                    updated = submission_queries.update_submission(
                        session, 
                        partial_submission.id, 
                        submission_type='google',
                        google_submission_id=response_id,
                        google_submission_value=answers
                    )
                    
                    if updated:
                        matched_count += 1
                        write_log(log_file, f"✓ Matched and updated submission ID {partial_submission.id} for uni_id {uni_id}")
                        write_log(log_file, f"  - Google response ID: {response_id}")
                    else:
                        write_log(log_file, f"✗ Failed to update submission ID {partial_submission.id}")
                else:
                    write_log(log_file, f"Response {response_id}: No matching partial submission for uni_id {uni_id}")
                    unmatched_responses.append(response_id)
            
            # Commit all updates
            session.commit()
            
            # Summary
            write_log(log_file, "\n=== Sync Summary ===")
            write_log(log_file, f"Total Google responses: {len(google_responses)}")
            write_log(log_file, f"Total partial submissions: {len(partial_submissions)}")
            write_log(log_file, f"Successfully matched: {matched_count}")
            write_log(log_file, f"Unmatched responses: {len(unmatched_responses)}")
            
            if unmatched_responses:
                write_log(log_file, f"Unmatched response IDs: {unmatched_responses}")
            
            write_log(log_file, "\n=== Sync Complete ===")
            
    except Exception as e:
        write_log_exception(log_file, e)
        write_log_traceback(log_file)

@router.get("/test-google-forms/{google_form_id}", status_code=status.HTTP_200_OK)
def test_fetch_form_responses(google_form_id: str):
    log_file = create_log_file("test google forms fetch")
    responses = fetch_form_responses(google_form_id, log_file)
    schema = fetch_schema(google_form_id)
    print("\n\n=== Responses ===\n")
    print(json.dumps(responses, indent=4, ensure_ascii=False))
    print("\n\n=== Schema ===\n")
    print(json.dumps(schema, indent=4, ensure_ascii=False))
    
    return schema

@router.post("/google/webhook", status_code=status.HTTP_200_OK)
async def google_forms_webhook(request: Request, background_tasks: BackgroundTasks):
    log_file = create_log_file("google forms webhook")
    try:
        write_log_title(log_file, "⚓ Google Forms Webhook Notification ⚓")
        
        body = await request.json()
        
        # Validate it's a Pub/Sub message
        if "message" not in body:
            write_log_json(log_file, {"status": "ignored", "reason": "not_pubsub_message", "body": body})
            return {"status": "ignored", "reason": "not_pubsub_message"}
        if "attributes" not in body["message"]:
            write_log_json(log_file, {"status": "ignored", "reason": "missing_attributes"})
            return {"status": "ignored", "reason": "missing_attributes"}
        
        write_log(log_file, f"Received Pub/Sub message: {body}")
        message = body["message"]
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
        
        # Sync form submissions in the background
        background_tasks.add_task(sync_form_submissions, form_id, log_file)
        write_log(log_file, f"Background task scheduled to sync submissions for form: {form_id}")
        
        return {
            "status": "received",
            "form_id": form_id,
            "event_type": event_type,
            "message_id": message_id
        }
        
    except KeyError as e:
        write_log_exception(log_file, e)
        write_log_traceback(log_file)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Missing required field: {str(e)}")
    except Exception as e:
        write_log_exception(log_file, e)
        write_log_traceback(log_file)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while processing the webhook")
