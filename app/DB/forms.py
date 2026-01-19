from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from .schema import Forms
from ..routers.models import Form_model


def create_form(session: Session, form: Form_model):
    """Create a new form in the database"""
    try:
        new_form = Forms(
            google_form_id=form.google_form_id,
            form_type=form.form_type,
            google_refresh_token=form.google_refresh_token,
            google_watch_id=form.google_watch_id,
            google_responders_url=form.google_responders_url,
            event_id=form.event_id
        )
        session.add(new_form)
        session.flush()
        return new_form
    except IntegrityError as e:
        session.rollback()
        print(f"IntegrityError in create_form: {e}...")
        return None

def update_form(session: Session, form_id: int, form: Form_model):
    """Update an existing form"""
    existing_form = session.scalar(select(Forms).where(Forms.id == form_id))
    if not existing_form:
        return None
    
    # Check if updating event_id would violate unique constraint
    if form.event_id != existing_form.event_id:
        existing_form_with_event = session.scalar(
            select(Forms).where(
                Forms.event_id == form.event_id,
                Forms.id != form_id
            )
        )
        if existing_form_with_event:
            return -1  # Conflict: event_id already exists
    
    print(f"Updating form: {existing_form.id}")
    existing_form.google_form_id = form.google_form_id
    existing_form.google_refresh_token = form.google_refresh_token
    existing_form.google_watch_id = form.google_watch_id
    existing_form.google_responders_url = form.google_responders_url
    existing_form.form_type = form.form_type
    existing_form.event_id = form.event_id
    
    session.flush()
    print(f"Updated form: {existing_form.id}")
    return existing_form

def get_forms(session: Session):
    """Get all forms from the database"""
    statement = select(Forms)
    forms = session.scalars(statement).all()
    return forms


def get_form_by_id(session: Session, form_id: int):
    """Get a specific form by ID"""
    statement = select(Forms).where(Forms.id == form_id)
    form = session.scalars(statement).first()
    return form


def get_form_by_event_id(session: Session, event_id: int):
    """Get a form by event ID"""
    statement = select(Forms).where(Forms.event_id == event_id)
    form = session.scalars(statement).first()
    return form

def get_form_by_google_form_id(session: Session, google_form_id: str):
    statement = select(Forms).where(Forms.google_form_id == google_form_id)
    form = session.scalars(statement).first()
    return form



def delete_form(session: Session, form_id: int):
    """Delete a form by ID"""
    form = session.scalar(select(Forms).where(Forms.id == form_id))
    if not form:
        return None
    
    session.delete(form)
    session.flush()
    return form

