from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from .schema import Submissions, t_forms_submissions


def create_submission(session: Session, form_id: int, submission_type: str, member_id: int):
    exists = session.execute(
        select(Submissions).where(
            Submissions.form_id == form_id,
            Submissions.member_id == member_id
        )
    ).first()
    if exists:
        return None

    submission = Submissions(
        form_id=form_id,
        member_id=member_id,
        is_accepted=0,
        submission_type=submission_type
    )
    session.add(submission)
    session.flush()
    return submission

def get_submission_by_form_and_member(session: Session, form_id: int, member_id: int):
    submission = session.execute(
        select(Submissions).where(
            Submissions.form_id == form_id,
            Submissions.member_id == member_id
        )
    ).scalar_one_or_none()
    return submission

def update_submission(session: Session, submission_id: int, submission_type: str = None, google_submission_id: str = None, google_submission_value: str = None):
    """Update submission fields including type and Google response data"""
    submission = session.execute(
        select(Submissions).where(Submissions.id == submission_id)
    ).scalar_one_or_none()
    
    if not submission:
        return None

    submission.submission_type = submission_type
    submission.google_submission_id = google_submission_id
    submission.google_submission_value = google_submission_value
    
    session.flush()
    return submission

# These function use the view 'form_submissions' not the table 'submissions'

def get_partial_submissions_by_form_id(session: Session, form_id: int):
    submissions = session.execute(
        select(t_forms_submissions).where(
            t_forms_submissions.c.form_id == form_id,
            t_forms_submissions.c.submission_type == 'partial'
        )
    ).all()
    return submissions

def get_submissions_by_event_id(session: Session, event_id: int):
    submissions = session.execute(
        select(t_forms_submissions).where(
            t_forms_submissions.c.event_id == event_id
        )
    ).all()
    return submissions