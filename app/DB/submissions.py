from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from .schema import Submissions


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