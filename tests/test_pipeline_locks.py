"""The pipeline's locks on real, separate MySQL connections: a commit always lets the next one in (bug #3)."""

from sqlalchemy.orm import Session

from app.DB import event_pipeline as queries


def test_a_commit_releases_the_booking_lock_for_another_connection(engine):
    with Session(engine) as first, Session(engine) as second:
        assert queries.lock_pipeline(first, "booking") is True
        # Held: a second transaction skipping locked rows does not get it.
        assert queries.lock_pipeline(second, "booking", wait=False) is False
        second.rollback()

        first.commit()
        # The commit ended the lock, whichever connection the pool hands out next.
        assert queries.lock_pipeline(second, "booking", wait=False) is True
        second.rollback()


def test_the_sweep_skips_a_round_while_another_worker_sweeps(engine):
    with Session(engine) as first, Session(engine) as second:
        assert queries.lock_pipeline(first, "sweep", wait=False) is True
        assert queries.lock_pipeline(second, "sweep", wait=False) is False
        # The booking lock is a different row.
        assert queries.lock_pipeline(second, "booking", wait=False) is True
        first.rollback()
        second.rollback()
