"""Rank pushes (app 1.0.3 build 6): a member entered the top 5, reached #1, or left the top 5.

The board is the app's Points tab: ``GET /points/members/total``
(``app/DB/points.py``) for the current semester, ranked as the app ranks it
(``src/lib/rank.ts``): competition ranking (1, 2, 2, 4), 0 points or fewer
unranked. A member's standing is ``first`` at rank 1 (a shared #1 too),
``top5`` at rank 5 or better (a shared #5 too), else ``out``.

Points change in a dozen places (attendance, custom points, event and
catalogue edits, an account deletion), and a member leaves the top 5 when
others pass them. So nothing hooks the writes: a sweep rule looks at the whole
board, and a bulk award is one look, one push per member per change of
standing however many rows it wrote.

``member_rank_standings`` keeps, per member and semester, what the sweep last
saw (``observed``, since when) and what the member's rank news is settled at
(``told``). Each sweep, while a public semester runs:

1. Ranks the board. Fewer than ``MIN_RANKED`` members with points: nothing
   (a board that small says nothing).
2. Leaves out a crowded tie: more than ``TIE_LIMIT`` members sharing one rank
   in the top 5 (the semester's first event gives 40 attendees the same
   points) is not news. Their standing stays as it was, in or out, until the
   tie breaks.
3. The semester's first look (deploy day, a new semester), or a mass change
   (more than ``MASS_LIMIT`` standings moving at once: a catalogue edit, a
   whole event set back to draft): takes what it sees as known and pushes
   nothing.
4. Otherwise records each standing that moved, and when one has held
   ``SETTLE`` (10 minutes) and differs from ``told``, queues what changed:
   out to top 5 ``rank_top5``; out or top 5 to #1 ``rank_first`` (so reaching
   #1 from outside says the most important one); top 5 or #1 to out
   ``rank_left``. Losing #1 inside the top 5 says nothing. ``told`` then
   becomes the new standing, also when the push reaches no phone (switch
   off, no app), so nobody gets old news after installing the app.

``rank_left`` goes only to a member whose last rank push sent this semester
said they were in (``rank_top5`` or ``rank_first``): nobody hears they left a
top 5 they were never told of (the first look's top 5, a muted member). At
most one rank push reaches a member per Riyadh day; another due that day waits
for the next, and is looked at again then. One waits at a time: a newer
change withdraws the one quiet hours still hold (``rank_changed``).

At delivery (``cause_gone``) the board is ranked again: a standing that no
longer says what the push says (an undo, a quick swap) skips it as
``rank_changed`` and gives ``told`` back, and the numbers are written then.

The kinds answer to the ``points`` switch, wait out quiet hours, expire after
12 hours and open ``gdgq://leaderboard``. They are not in the shared daily cap
(``cases.CAPPED``): ranks move on event days, when certificates, content and
summaries would use it up. See docs/PUSH.md.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import config
from app.DB import points as points_queries
from app.DB.schema import MemberRankStandings, PushKind, PushNotifications, PushStatus, RankStanding, Semesters
from app.DB.semesters import get_semester_started_by
from app.services.push import clock, links, messages, outbox
from app.services.push.outbox import Push
from app.services.push.triggers import _sweep_rule

logger = logging.getLogger(__name__)

KINDS = frozenset({PushKind.RANK_TOP5, PushKind.RANK_FIRST, PushKind.RANK_LEFT})
# What a member is told they are in; "left" only follows one of these.
IN_KINDS = frozenset({PushKind.RANK_TOP5, PushKind.RANK_FIRST})
# What changed, from what the member was told to what holds now. First to top 5 (losing #1 in the top 5): nothing.
KIND_FOR = {
    (RankStanding.OUT, RankStanding.TOP5): PushKind.RANK_TOP5,
    (RankStanding.OUT, RankStanding.FIRST): PushKind.RANK_FIRST,
    (RankStanding.TOP5, RankStanding.FIRST): PushKind.RANK_FIRST,
    (RankStanding.TOP5, RankStanding.OUT): PushKind.RANK_LEFT,
    (RankStanding.FIRST, RankStanding.OUT): PushKind.RANK_LEFT,
}

TOP = 5
# A standing must hold this long before it is told: an undo or a quick swap sends nothing.
SETTLE = timedelta(minutes=10)
TTL = timedelta(hours=12)
# Flood guards. A board with fewer members with points than this says nothing.
MIN_RANKED = 20
# More members than this sharing one rank in the top 5 (a tie bigger than the top 5 itself) is not news.
TIE_LIMIT = 5
# More standings than this moving in one look is a mass change (a catalogue edit): taken as known, nothing pushed.
MASS_LIMIT = 10
# Skip reasons after which the member was not told: ``told`` goes back to what it was before the push.
UNTOLD = frozenset({"rank_changed", "daily_cap"})


@dataclass(frozen=True)
class Place:
    rank: int
    points: int


class Board:
    """A semester's members leaderboard, ranked as the app ranks it."""

    def __init__(self, places: dict[int, Place]) -> None:
        self.places = places
        sizes = Counter(place.rank for place in places.values() if place.rank <= TOP)
        crowded = {rank for rank, size in sizes.items() if size > TIE_LIMIT}
        # Members in a crowded tie: their standing is left as it was.
        self.crowded = {member_id for member_id, place in places.items() if place.rank in crowded}

    @classmethod
    def of(cls, session: Session, semester_id: str) -> "Board":
        rows = points_queries.get_members_points_semester(session, semester_id)
        totals = sorted(
            ((int(row["total_points"] or 0), row["member_id"]) for row in rows), key=lambda total: -total[0]
        )
        places: dict[int, Place] = {}
        previous: Place | None = None
        for index, (points, member_id) in enumerate(totals):
            if points <= 0:
                break  # 0 points or fewer: not ranked
            rank = previous.rank if previous is not None and previous.points == points else index + 1
            previous = places[member_id] = Place(rank, points)
        return cls(places)

    def standing(self, member_id: int) -> RankStanding:
        place = self.places.get(member_id)
        if place is None or place.rank > TOP:
            return RankStanding.OUT
        return RankStanding.FIRST if place.rank == 1 else RankStanding.TOP5


def running_semester(session: Session, now: datetime) -> Semesters | None:
    """The semester the members' Points tab shows, while it runs (today between its dates, Riyadh): else None.

    A private current semester is skipped: members see an older public one, and
    pushes about that one would be old news.
    """
    today = clock.to_riyadh(now).date()
    semester = get_semester_started_by(session, today)
    if semester is None or not semester.is_public or today > semester.end_date:
        return None
    return semester


def text_for(kind: PushKind, place: Place | None) -> messages.Text:
    if kind == PushKind.RANK_TOP5 and place is not None:
        return messages.rank_top5(place.rank)
    if kind == PushKind.RANK_FIRST and place is not None:
        return messages.rank_first(place.points)
    return messages.rank_left()


# ---------------------------------------------------------------- sweep rule


def enqueue_rank_changes(session: Session, now: datetime, result) -> None:
    if not config.push_enabled:
        return
    semester = running_semester(session, now)
    if semester is None:
        return
    board = Board.of(session, semester.id)
    if len(board.places) < MIN_RANKED:
        return
    stored = {
        row.member_id: row
        for row in session.scalars(
            select(MemberRankStandings).where(MemberRankStandings.semester_id == semester.id)
        ).all()
    }
    in_top = {member_id for member_id, place in board.places.items() if place.rank <= TOP}
    seen = {member_id: board.standing(member_id) for member_id in (set(stored) | in_top) - board.crowded}
    moved = {
        member_id: standing
        for member_id, standing in seen.items()
        if standing != (stored[member_id].observed if member_id in stored else RankStanding.OUT)
    }
    if not stored or len(moved) > MASS_LIMIT:
        _take_as_known(session, semester, stored, seen, now)
        if seen:
            logger.info(
                "Rank pushes: %s for semester %s, %d standing(s) taken as known",
                "first look" if not stored else "mass change",
                semester.hijri_code,
                len(moved),
            )
        return
    for member_id, standing in moved.items():
        row = stored.get(member_id)
        if row is None:
            row = stored[member_id] = MemberRankStandings(
                semester_id=semester.id, member_id=member_id, told=RankStanding.OUT, told_at=now, pushes=0
            )
            session.add(row)
        row.observed, row.observed_since = standing, now
    session.flush()
    due = [
        row
        for member_id, row in sorted(stored.items())
        if member_id in seen and row.observed != row.told and now - row.observed_since >= SETTLE
    ]
    if due:
        result.push_rank += _tell(session, semester, board, due, now)


def _take_as_known(
    session: Session, semester: Semesters, stored: dict[int, MemberRankStandings], seen: dict, now: datetime
) -> None:
    """Every standing seen is what the member is taken to know; nothing is pushed. A crowded tie is left as it was."""
    for member_id, standing in seen.items():
        row = stored.get(member_id)
        if row is None:
            if standing != RankStanding.OUT:
                session.add(
                    MemberRankStandings(
                        semester_id=semester.id,
                        member_id=member_id,
                        observed=standing,
                        observed_since=now,
                        told=standing,
                        told_at=now,
                        pushes=0,
                    )
                )
            continue
        if row.observed != standing:
            row.observed, row.observed_since = standing, now
        _settle(row, now)
    session.flush()


def _settle(row: MemberRankStandings, now: datetime) -> None:
    if row.told != row.observed:
        row.told, row.told_at = row.observed, now


def _tell(session: Session, semester: Semesters, board: Board, due: list[MemberRankStandings], now: datetime) -> int:
    member_ids = {row.member_id for row in due}
    waiting = {
        push.member_id: push
        for push in session.scalars(
            select(PushNotifications).where(
                PushNotifications.member_id.in_(member_ids),
                PushNotifications.kind.in_(KINDS),
                PushNotifications.status == PushStatus.PENDING,
            )
        ).all()
    }
    sent = _sent(session, member_ids)
    readers = outbox.reachable(session, "points", member_ids)
    # The Riyadh day it would reach the phone: after quiet hours.
    day = clock.to_riyadh(clock.after_quiet_hours(now)).date()
    queued = 0
    for row in due:
        pending = waiting.get(row.member_id)
        if pending is not None:
            if (pending.payload.get("cause") or {}).get("to") == row.observed.value:
                _settle(row, now)  # the push waiting already says it
                continue
            if not _withdraw(session, pending, row, now):
                continue  # out for delivery: the next look sees how it went
            if row.told == row.observed:
                continue  # undone: the push that said otherwise is withdrawn
        kind = KIND_FOR.get((row.told, row.observed))
        history = sent.get(row.member_id, [])
        told_in = next((k for k, _, s in reversed(history) if s == semester.id), None) in IN_KINDS
        place = board.places.get(row.member_id)
        if kind is None or (kind == PushKind.RANK_LEFT and not told_in):
            _settle(row, now)  # nothing to say: #1 lost in the top 5, or a top 5 they were never told of
            continue
        if kind != PushKind.RANK_LEFT and place is None:
            continue
        if any(clock.to_riyadh(at).date() == day for _, at, _ in history):
            continue  # one a day: the next sweeps look at it again
        row.pushes += 1
        push = Push(
            member_id=row.member_id,
            dedupe_key=f"{kind.value}:{semester.hijri_code}:{row.member_id}:{row.pushes}",
            cause={"semester_id": semester.id, "from": row.told.value, "to": row.observed.value, "n": row.pushes},
        )
        queued += outbox.enqueue(
            session,
            kind,
            [push],
            text=text_for(kind, place),
            url=links.LEADERBOARD_URL,
            thread_id="points",
            expires_at=now + TTL,
            now=now,
            reachable_ids=readers,
        )
        _settle(row, now)
    session.flush()
    return queued


def _sent(session: Session, member_ids: set[int]) -> dict[int, list[tuple[PushKind, datetime, str | None]]]:
    """{member: [(kind, sent_at, semester id)]}: every rank push sent to them, oldest first."""
    rows = session.execute(
        select(
            PushNotifications.member_id, PushNotifications.kind, PushNotifications.sent_at, PushNotifications.payload
        )
        .where(
            PushNotifications.member_id.in_(member_ids),
            PushNotifications.kind.in_(KINDS),
            PushNotifications.status == PushStatus.SENT,
        )
        .order_by(PushNotifications.sent_at, PushNotifications.id)
    ).all()
    found: dict[int, list[tuple[PushKind, datetime, str | None]]] = {}
    for member_id, kind, sent_at, payload in rows:
        semester_id = ((payload or {}).get("cause") or {}).get("semester_id")
        found.setdefault(member_id, []).append((kind, sent_at, semester_id))
    return found


def _withdraw(session: Session, pending: PushNotifications, row: MemberRankStandings, now: datetime) -> bool:
    """Skip a rank push still waiting (quiet hours) whose news no longer holds; False when it is out for delivery.

    A locking read: a claim holding the row, or one that has claimed it since
    this sweep's snapshot, leaves it alone, and the member waits for the next look.
    """
    locked = session.scalar(
        select(PushNotifications)
        .where(
            PushNotifications.id == pending.id,
            PushNotifications.status == PushStatus.PENDING,
            PushNotifications.attempts == 0,
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if locked is None:
        return False
    locked.status, locked.last_error = PushStatus.SKIPPED, "rank_changed"
    cause = locked.payload.get("cause") or {}
    if cause.get("n") == row.pushes and cause.get("from"):
        row.told, row.told_at = RankStanding(cause["from"]), now
    return True


SWEEP_RULES = [_sweep_rule(enqueue_rank_changes)]


# ---------------------------------------------------------------- at delivery


def cause_gone(session: Session, row: PushNotifications, now: datetime, boards: dict[str, Board]) -> str | None:
    """Why this rank push should no longer go out, or None (its text then written with the numbers as they are).

    ``boards`` ranks each semester once per claim batch.
    """
    cause = row.payload.get("cause") or {}
    semester = running_semester(session, now)
    if semester is None or semester.id != cause.get("semester_id"):
        return "semester_over"
    board = boards.get(semester.id)
    if board is None:
        board = boards[semester.id] = Board.of(session, semester.id)
    if board.standing(row.member_id).value != cause.get("to"):
        return "rank_changed"
    start, end = clock.riyadh_day_bounds(now)
    sent_today = session.scalar(
        select(PushNotifications.id)
        .where(
            PushNotifications.member_id == row.member_id,
            PushNotifications.kind.in_(KINDS),
            PushNotifications.status == PushStatus.SENT,
            PushNotifications.sent_at >= start,
            PushNotifications.sent_at < end,
        )
        .limit(1)
    )
    if sent_today is not None:
        return "daily_cap"
    row.payload = {**row.payload, **text_for(row.kind, board.places.get(row.member_id))}
    return None


def untell(session: Session, row: PushNotifications, now: datetime) -> None:
    """A rank push skipped before the member got it (``UNTOLD``): ``told`` goes back to what it was before it.

    Only while the standing is still as this push left it (its ``pushes`` and
    ``told``): a sweep that has moved on since is not undone.
    """
    cause = row.payload.get("cause") or {}
    if not cause.get("from") or not cause.get("to"):
        return
    session.execute(
        update(MemberRankStandings)
        .where(
            MemberRankStandings.semester_id == cause.get("semester_id"),
            MemberRankStandings.member_id == row.member_id,
            MemberRankStandings.pushes == cause.get("n"),
            MemberRankStandings.told == RankStanding(cause["to"]),
        )
        .values(told=RankStanding(cause["from"]), told_at=now)
        .execution_options(synchronize_session=False)
    )
