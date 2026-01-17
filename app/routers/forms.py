from fastapi import APIRouter, Depends, HTTPException, status, Request
from app.routers.models import Form_model, NotFoundResponse
from app.DB import forms as form_queries
from ..DB.main import SessionLocal
from app.routers.logging import write_log_exception, write_log_traceback, create_log_file, write_log_title, write_log_json, write_log
from app.helpers import admin_guard, is_admin
from app.config import config
import json

router = APIRouter()


@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Form_model])
def get_all_forms():
    with SessionLocal() as session:
        forms = form_queries.get_forms(session)
    return forms


@router.get("/{form_id:int}", status_code=status.HTTP_200_OK, response_model=Form_model, responses={404: {"model": NotFoundResponse, "description": "Form not found"}})
def get_form_by_id(form_id: int):
    with SessionLocal() as session:
        form = form_queries.get_form_by_id(session, form_id)
        if not form:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Form with id {form_id} not found")
    return form


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=Form_model)
def create_form(form: Form_model, credentials = Depends(admin_guard)):

    with SessionLocal() as session:
        log_file = create_log_file("create form")
        try:
            write_log_title(log_file, "Creating New Form")
            new_form = form_queries.create_form(session, form)
            if new_form is None:
                write_log_exception(log_file, f"HTTP 409: Form with event_id {form.event_id} already exists")
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Form with event_id {form.event_id} already exists")
            session.commit()
            return new_form
        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while creating the form")
        finally:
            write_log_json(log_file, form.model_dump())


@router.put("/{form_id:int}", status_code=status.HTTP_200_OK, response_model=Form_model, responses={404: {"model": NotFoundResponse, "description": "Form not found"}, 409: {"model": NotFoundResponse, "description": "Form with event_id already exists"}})
def update_form(form_id: int, form: Form_model, credentials = Depends(admin_guard)):
    with SessionLocal() as session:
        log_file = create_log_file("update form")
        try:
            write_log_title(log_file, f"Updating Form {form_id}")
            updated_form = form_queries.update_form(session, form_id, form)
            if updated_form is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Form with id {form_id} not found")
            if updated_form == -1:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Form with event_id {form.event_id} already exists")
            session.commit()
            return updated_form
        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while updating the form")
        finally:
            write_log_json(log_file, form.model_dump())


@router.delete("/{form_id:int}", status_code=status.HTTP_200_OK, response_model=Form_model, responses={404: {"model": NotFoundResponse, "description": "Form not found"}})
def delete_form(form_id: int, credentials = Depends(admin_guard)):
    with SessionLocal() as session:
        log_file = create_log_file("delete form")
        try:
            write_log_title(log_file, f"Deleting Form {form_id}")
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
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while deleting the form")


@router.post("/google/webhook", status_code=status.HTTP_200_OK)
async def google_forms_webhook(request: Request):
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
        
        # TODO: Implement form sync logic here
        # For now, just log the notification
        write_log_json(log_file, {
            "status": "received",
            "form_id": form_id,
            "watch_id": watch_id,
            "event_type": event_type,
            "message_id": message_id,
            "publish_time": publish_time,
            "subscription": subscription
        })
        
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

