from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from app.DB.schema import Events, Forms
from app.routers.models import Form_model
from app.exceptions import EventNotFound, FormNotFound, FormNotFoundById, DataIntegrityError


def create_form(session: Session, form: Form_model):
    """Create a new form in the database"""
    try:
        new_form = Forms(
            google_form_id=form.google_form_id,
            form_type=form.form_type,
            google_refresh_token=form.google_refresh_token,
            google_watch_id=form.google_watch_id,
            google_responders_url=form.google_responders_url,
            event_id=form.event_id,
        )
        session.add(new_form)
        session.flush()
        return new_form
    except IntegrityError:
        # This is an impossible state in normal operation - events always create forms.
        # If we reach here, something is wrong (race condition, manual DB manipulation, or bug).
        raise DataIntegrityError(f"Cannot create form for event {form.event_id} - form may already exist")


def update_form(session: Session, form_id: int, form: Form_model):
    """Update an existing form. Note: event_id is not updatable (forms are 1-1 with events)."""
    existing_form = session.scalar(select(Forms).where(Forms.id == form_id))
    if not existing_form:
        raise FormNotFoundById(form_id)

    existing_form.google_form_id = form.google_form_id
    existing_form.google_refresh_token = form.google_refresh_token
    existing_form.google_watch_id = form.google_watch_id
    existing_form.google_responders_url = form.google_responders_url
    existing_form.form_type = form.form_type
    # event_id is NOT updatable - forms are permanently bound to their events

    session.flush()
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
    event = session.scalar(select(Events).where(Events.id == event_id))
    if not event:
        raise EventNotFound(event_id)

    statement = select(Forms).where(Forms.event_id == event_id)
    form = session.scalars(statement).first()
    if not form:
        # Legacy events may not have forms - this is valid for old production data
        raise FormNotFound(event_id)
    return form


def get_form_by_google_form_id(session: Session, google_form_id: str):
    statement = select(Forms).where(Forms.google_form_id == google_form_id)
    form = session.scalars(statement).first()
    return form
