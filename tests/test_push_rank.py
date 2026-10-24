"""Rank pushes (app/services/push/rank.py): entering the top 5, reaching #1, leaving the top 5.

The clock is naive UTC 2026-07-20 09:00 (a Monday, 12:00 in Riyadh), in
Summer 2026 (475, 2026-06-28 to 2026-08-20). Every point is recorded on one
closed custom-points event of that semester; ``leaders`` adds 20 members with a
point each, ranked last, so the board is big enough to count.
"""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.DB import members as member_queries
from app.DB import push as push_queries
from app.DB.schema import (
    Actions,
    Events,
    EventsLocationType,
    EventsStatus,
    MemberRankStandings,
    Members,
    MembersLogs,
    Modifications,
    ModificationsType,
    PushKind,
    PushNotifications,
    PushStatus,
    RankStanding,
    Semesters,
    SemesterTerm,
)
from app.services import pipeline_sweep
from app.services.pipeline_sweep import run_sweep
from app.services.push import delivery, links, messages, outbox, rank
from app.services.push.outbox import Push
from tests.push_support import add_device, attendance_log, deliver, make_event, make_member, set_prefs
from tests.utils import assert_2xx

LRI, PDI = "⁦", "⁩"
TWELVE_HOURS = timedelta(hours=12)
EIGHT_AM_TOMORROW = datetime(2026, 7, 21, 5, 0)  # 08:00 Riyadh on 2026-07-21


@pytest.fixture
def season(db_session) -> Events:
    """A closed custom-points event of Summer 2026: every point in these tests is recorded on it."""
    return make_event(
        db_session,
        start=datetime(2026, 7, 10, 16, 0),
        status=EventsStatus.CLOSED,
        location_type=EventsLocationType.NONE,
        name="Points",
    )


def app_user(session, **switches) -> Members:
    member = make_member(session)
    add_device(session, member)
    if switches:
        set_prefs(session, member, **switches)
    return member


def give(session, event: Events, member: Members, points: int) -> MembersLogs:
    log = attendance_log(session, event, points=points)
    entry = MembersLogs(member_id=member.id, log_id=log.id, date=datetime(2026, 7, 10, 16, 30))
    session.add(entry)
    session.flush()
    return entry


def take_back(session, entry: MembersLogs) -> None:
    session.delete(entry)
    session.commit()


def leaders(session, event: Events, *totals: int) -> list[Members]:
    """Members on a phone with these totals, and ``MIN_RANKED`` more with a point each (ranked last, no phone)."""
    found = []
    for total in totals:
        member = app_user(session)
        give(session, event, member, total)
        found.append(member)
    for _ in range(rank.MIN_RANKED):
        give(session, event, make_member(session), 1)
    session.commit()
    return found


def look(session, frozen, **advance) -> int:
    """One sweep at the frozen clock (moved on first by ``advance``): the rank pushes it queued."""
    if advance:
        frozen.advance(**advance)
    result = run_sweep(session, frozen.now)
    session.commit()
    return result.push_rank


def held(session, frozen) -> int:
    """Sweeps every 5 minutes until a change seen now has held 10 minutes: the rank pushes they queued."""
    return look(session, frozen) + look(session, frozen, minutes=5) + look(session, frozen, minutes=5)


def rank_rows(session) -> list[PushNotifications]:
    session.expire_all()
    return list(
        session.scalars(
            select(PushNotifications)
            .where(PushNotifications.kind.in_(rank.KINDS))
            .order_by(PushNotifications.created_at, PushNotifications.id)
        ).all()
    )


def standing_row(session, member: Members) -> MemberRankStandings | None:
    session.expire_all()
    return session.scalar(select(MemberRankStandings).where(MemberRankStandings.member_id == member.id))


def standing(session, member: Members) -> MemberRankStandings:
    row = standing_row(session, member)
    assert row is not None, "no standing stored for the member"
    return row


# ---------------------------------------------------------------- entering, #1, leaving


def test_entering_the_top_5_is_told_once_it_has_held_10_minutes(db_session, push_on, apns_mock, frozen, season):
    top = leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    db_session.commit()
    assert look(db_session, frozen) == 0  # the first look: what it sees is taken as known

    give(db_session, season, climber, 45)  # 75: #4
    db_session.commit()
    assert look(db_session, frozen) == 0  # seen, not held yet
    assert look(db_session, frozen, minutes=5) == 0
    assert look(db_session, frozen, minutes=5) == 1
    [row] = rank_rows(db_session)
    assert (row.kind, row.member_id) == (PushKind.RANK_TOP5, climber.id)
    assert row.dedupe_key == f"rank_top5:475:{climber.id}:1"
    assert row.payload["url"] == "gdgq://leaderboard"
    assert (row.payload["thread_id"], row.payload["switch"]) == ("points", "points")
    assert row.payload["cause"] == {"semester_id": season.semester_id, "from": "out", "to": "top5", "n": 1}
    assert (row.not_before, row.expires_at) == (frozen.now, frozen.now + TWELVE_HOURS)

    assert deliver().sent == 1
    [row] = rank_rows(db_session)
    assert row.payload["en"] == {
        "title": "You're in the top 5!",
        "body": "You're #4 this semester. Keep going, first place is close!",
    }
    assert row.payload["ar"] == {
        "title": "دخلت الخمسة الأوائل!",
        "body": f"صرت رقم {LRI}4{PDI} في ترتيب الفصل. كمّل، الأول قريب!",
    }
    [request] = apns_mock.requests
    assert request.headers["apns-priority"] == "5"
    assert apns_mock.payloads()[0]["kind"] == "rank_top5"
    assert apns_mock.payloads()[0]["url"] == "gdgq://leaderboard"

    # Told once; the #5 it pushed out (60, now #6) was never told it was in, so hears nothing.
    assert look(db_session, frozen, minutes=5) == 0
    assert [r.member_id for r in rank_rows(db_session)] == [climber.id]
    assert standing(db_session, top[4]).told == RankStanding.OUT


def test_reaching_first_says_the_points(db_session, push_on, apns_mock, frozen, season):
    top = leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    db_session.commit()
    look(db_session, frozen)

    give(db_session, season, climber, 81)  # 111: from outside the top 5 straight to #1
    db_session.commit()
    assert held(db_session, frozen) == 1
    [row] = rank_rows(db_session)
    assert (row.kind, row.payload["cause"]["from"], row.payload["cause"]["to"]) == (PushKind.RANK_FIRST, "out", "first")
    deliver()
    [row] = rank_rows(db_session)
    assert row.payload["en"] == {
        "title": "You're #1!",
        "body": "You lead the members this semester with 111 points. Stay on top!",
    }
    assert row.payload["ar"] == {
        "title": "أنت الأول!",
        "body": f"تصدّرت ترتيب الأعضاء هذا الفصل بـ{LRI}111{PDI} نقطة. خلّك فوق!",
    }
    # The old #1 is #2 now, still in the top 5: losing #1 sends nothing.
    assert standing(db_session, top[0]).told == RankStanding.TOP5


def test_a_shared_first_counts(db_session, push_on, apns_mock, frozen, season):
    top = leaders(db_session, season, 100, 90, 80, 70, 60)
    look(db_session, frozen)
    give(db_session, season, top[1], 10)  # 100, shared #1
    db_session.commit()
    assert held(db_session, frozen) == 1
    [row] = rank_rows(db_session)
    assert (row.kind, row.member_id, row.payload["cause"]["from"]) == (PushKind.RANK_FIRST, top[1].id, "top5")
    deliver()
    assert rank_rows(db_session)[0].payload["en"]["body"] == (
        "You lead the members this semester with 100 points. Stay on top!"
    )
    assert standing(db_session, top[0]).told == RankStanding.FIRST  # still #1, shared: nothing new


def test_a_shared_fifth_counts(db_session, push_on, apns_mock, frozen, season):
    top = leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    db_session.commit()
    look(db_session, frozen)
    give(db_session, season, climber, 30)  # 60: #5 with top[4]
    db_session.commit()
    assert held(db_session, frozen) == 1
    deliver()
    [row] = rank_rows(db_session)
    assert (row.kind, row.member_id) == (PushKind.RANK_TOP5, climber.id)
    assert row.payload["en"]["body"] == "You're #5 this semester. Keep going, first place is close!"
    assert standing(db_session, top[4]).told == RankStanding.TOP5


def test_leaving_the_top_5_after_being_told_in(db_session, push_on, apns_mock, frozen, season):
    leaders(db_session, season, 100, 90, 80, 70, 60)
    climber, chaser = app_user(db_session), app_user(db_session)
    give(db_session, season, climber, 30)
    give(db_session, season, chaser, 20)
    db_session.commit()
    look(db_session, frozen)
    give(db_session, season, climber, 35)  # 65: #5
    db_session.commit()
    assert held(db_session, frozen) == 1
    deliver()

    frozen.set(datetime(2026, 7, 21, 9, 0))  # the next day, 12:00 Riyadh
    give(db_session, season, chaser, 50)  # 70: climber is #6 now
    db_session.commit()
    assert held(db_session, frozen) == 2  # the chaser in, the climber out
    deliver()
    found = {row.member_id: row for row in rank_rows(db_session) if row.not_before >= frozen.now - timedelta(hours=1)}
    assert found[chaser.id].kind == PushKind.RANK_TOP5
    left = found[climber.id]
    assert (left.kind, left.status, left.dedupe_key) == (
        PushKind.RANK_LEFT,
        PushStatus.SENT,
        f"rank_left:475:{climber.id}:2",
    )
    assert left.payload["en"] == {
        "title": "You can get back in!",
        "body": "You dropped out of the top 5, but we want to see you first, always. Your next event is your chance!",
    }
    assert left.payload["ar"] == {
        "title": "تقدر ترجع!",
        "body": "طلعت من الخمسة الأوائل، بس نبي نشوفك دايم الأول. الفعالية الجاية فرصتك!",
    }


def test_nobody_hears_they_left_a_top_5_they_were_never_told_of(db_session, push_on, apns_mock, frozen, season):
    top = leaders(db_session, season, 100, 90, 80, 70, 60)
    muted = app_user(db_session, points=False)
    give(db_session, season, muted, 20)
    chaser = make_member(db_session)  # no app
    give(db_session, season, chaser, 10)
    db_session.commit()
    look(db_session, frozen)  # top[4] is in the first look's top 5: taken as known, never pushed

    give(db_session, season, muted, 45)  # 65: #5, but the switch is off
    db_session.commit()
    assert held(db_session, frozen) == 0
    assert standing(db_session, muted).told == RankStanding.TOP5  # settled anyway: no old news later

    push_queries.save_prefs(db_session, muted.id, {"points": True}, frozen.now)
    give(db_session, season, chaser, 100)  # 110: muted (#6) and top[4] (#7) leave the top 5
    db_session.commit()
    assert held(db_session, frozen) == 0
    assert rank_rows(db_session) == []
    assert standing(db_session, muted).told == RankStanding.OUT
    assert standing(db_session, top[4]).told == RankStanding.OUT


def test_losing_first_inside_the_top_5_sends_nothing_and_taking_it_back_is_news(
    db_session, push_on, apns_mock, frozen, season
):
    top = leaders(db_session, season, 100, 90, 80, 70, 60)
    look(db_session, frozen)
    passed = give(db_session, season, top[1], 20)  # 110: top[0] is #2
    db_session.commit()
    assert held(db_session, frozen) == 1  # top[1] reached #1; top[0] hears nothing
    assert [(r.kind, r.member_id) for r in rank_rows(db_session)] == [(PushKind.RANK_FIRST, top[1].id)]
    assert standing(db_session, top[0]).told == RankStanding.TOP5

    take_back(db_session, passed)  # top[0] is #1 again
    frozen.set(datetime(2026, 7, 21, 9, 0))
    assert held(db_session, frozen) == 1
    assert [(r.kind, r.member_id) for r in rank_rows(db_session)][-1] == (PushKind.RANK_FIRST, top[0].id)


# ---------------------------------------------------------------- hold time, undo, swaps


def test_an_undo_within_10_minutes_sends_nothing(db_session, push_on, apns_mock, frozen, season):
    leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    db_session.commit()
    look(db_session, frozen)
    mistake = give(db_session, season, climber, 45)
    db_session.commit()
    assert look(db_session, frozen) == 0
    take_back(db_session, mistake)  # 5 minutes later: the scan was wrong
    assert look(db_session, frozen, minutes=5) == 0
    assert look(db_session, frozen, minutes=5) == 0
    assert look(db_session, frozen, minutes=5) == 0
    assert rank_rows(db_session) == []
    assert standing(db_session, climber).told == RankStanding.OUT


def test_a_quick_swap_at_the_top_sends_nothing(db_session, push_on, apns_mock, frozen, season):
    top = leaders(db_session, season, 100, 95, 80, 70, 60)
    look(db_session, frozen)
    give(db_session, season, top[1], 10)  # 105: top[1] #1
    db_session.commit()
    assert look(db_session, frozen) == 0
    give(db_session, season, top[0], 10)  # 110: back to how it was, 5 minutes later
    db_session.commit()
    assert look(db_session, frozen, minutes=5) == 0
    assert look(db_session, frozen, minutes=5) == 0
    assert look(db_session, frozen, minutes=5) == 0
    assert rank_rows(db_session) == []


def test_an_undo_overnight_withdraws_the_waiting_push(db_session, push_on, apns_mock, frozen, season):
    leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    db_session.commit()
    look(db_session, frozen)
    frozen.set(datetime(2026, 7, 20, 19, 30))  # 22:30 Riyadh
    mistake = give(db_session, season, climber, 30)  # 60: #5 with top[4], who stays in
    db_session.commit()
    assert held(db_session, frozen) == 1
    [row] = rank_rows(db_session)
    assert row.not_before == EIGHT_AM_TOMORROW  # quiet hours

    frozen.set(datetime(2026, 7, 20, 23, 0))  # 02:00 Riyadh
    take_back(db_session, mistake)
    assert held(db_session, frozen) == 0
    [row] = rank_rows(db_session)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "rank_changed")
    assert standing(db_session, climber).told == RankStanding.OUT
    frozen.set(EIGHT_AM_TOMORROW)
    deliver()
    assert apns_mock.requests == []


def test_delivery_checks_again_and_gives_the_news_back(db_session, push_on, apns_mock, frozen, season):
    leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    db_session.commit()
    look(db_session, frozen)
    frozen.set(datetime(2026, 7, 20, 19, 30))  # 22:30 Riyadh
    mistake = give(db_session, season, climber, 30)  # 60: #5 with top[4], who stays in
    db_session.commit()
    assert held(db_session, frozen) == 1

    frozen.set(datetime(2026, 7, 21, 4, 58))  # 07:58: undone two minutes before it goes, no sweep since
    take_back(db_session, mistake)
    frozen.set(EIGHT_AM_TOMORROW)
    deliver()
    [row] = rank_rows(db_session)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "rank_changed")
    assert standing(db_session, climber).told == RankStanding.OUT  # not told: given back
    assert held(db_session, frozen) == 0
    assert apns_mock.requests == []


# ---------------------------------------------------------------- one a day, priority, quiet hours


def test_one_rank_push_a_day_the_next_waits_for_tomorrow(db_session, push_on, apns_mock, frozen, season):
    leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    db_session.commit()
    look(db_session, frozen)
    give(db_session, season, climber, 35)  # 65: #5
    db_session.commit()
    assert held(db_session, frozen) == 1
    deliver()

    frozen.set(datetime(2026, 7, 20, 11, 0))  # 14:00 Riyadh
    give(db_session, season, climber, 50)  # 115: #1
    db_session.commit()
    assert held(db_session, frozen) == 0  # one a day
    assert look(db_session, frozen, hours=6) == 0  # 20:10, still today

    frozen.set(datetime(2026, 7, 20, 21, 5))  # 00:05 Riyadh, the next day: queued for 08:00
    assert look(db_session, frozen) == 1
    first = rank_rows(db_session)[-1]
    assert (first.kind, first.not_before) == (PushKind.RANK_FIRST, EIGHT_AM_TOMORROW)
    frozen.set(EIGHT_AM_TOMORROW)
    assert deliver().sent == 1
    assert [r.kind for r in rank_rows(db_session) if r.status == PushStatus.SENT] == [
        PushKind.RANK_TOP5,
        PushKind.RANK_FIRST,
    ]


def test_the_most_important_goes_when_two_are_due(db_session, push_on, apns_mock, frozen, season):
    """In the top 5 at 22:30, #1 at 23:00: the top-5 push quiet hours hold is withdrawn, and #1 goes at 08:00."""
    leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    db_session.commit()
    look(db_session, frozen)
    frozen.set(datetime(2026, 7, 20, 19, 30))
    give(db_session, season, climber, 45)  # 75: #4
    db_session.commit()
    assert held(db_session, frozen) == 1
    frozen.set(datetime(2026, 7, 20, 20, 0))
    give(db_session, season, climber, 50)  # 125: #1
    db_session.commit()
    assert held(db_session, frozen) == 1
    top5, first = rank_rows(db_session)
    assert (top5.kind, top5.status, top5.last_error) == (PushKind.RANK_TOP5, PushStatus.SKIPPED, "rank_changed")
    assert (first.kind, first.not_before, first.payload["cause"]["from"]) == (
        PushKind.RANK_FIRST,
        EIGHT_AM_TOMORROW,
        "out",
    )
    frozen.set(EIGHT_AM_TOMORROW)
    assert deliver().sent == 1
    assert len(apns_mock.requests) == 1


def test_quiet_hours_hold_it_and_the_numbers_are_the_ones_at_delivery(db_session, push_on, apns_mock, frozen, season):
    leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    db_session.commit()
    look(db_session, frozen)
    frozen.set(datetime(2026, 7, 20, 19, 30))  # 22:30 Riyadh
    give(db_session, season, climber, 45)  # 75: #4
    db_session.commit()
    assert held(db_session, frozen) == 1
    [row] = rank_rows(db_session)
    assert row.payload["en"]["body"].startswith("You're #4 ")
    frozen.set(datetime(2026, 7, 21, 0, 0))  # 03:00
    give(db_session, season, climber, 20)  # 95: #2, still in the top 5
    db_session.commit()
    frozen.set(EIGHT_AM_TOMORROW - timedelta(minutes=1))
    assert deliver().sent == 0
    frozen.set(EIGHT_AM_TOMORROW)
    assert deliver().sent == 1
    [row] = rank_rows(db_session)
    assert row.payload["en"]["body"] == "You're #2 this semester. Keep going, first place is close!"


def test_rank_pushes_are_not_held_to_the_shared_daily_cap(db_session, push_on, apns_mock, frozen, season):
    leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    for n in range(3):
        db_session.add(
            PushNotifications(
                member_id=climber.id,
                kind=PushKind.WEEKLY_SUMMARY,
                dedupe_key=f"weekly_summary:rank-{n}:{climber.id}",
                payload={},
                status=PushStatus.SENT,
                not_before=frozen.now - timedelta(hours=1),
                expires_at=frozen.now + timedelta(hours=1),
                created_at=frozen.now - timedelta(hours=1),
                sent_at=frozen.now - timedelta(hours=1),
            )
        )
    db_session.commit()
    look(db_session, frozen)
    give(db_session, season, climber, 45)
    db_session.commit()
    assert held(db_session, frozen) == 1
    assert deliver().sent == 1


# ---------------------------------------------------------------- silent starts


def test_the_first_look_stores_the_standings_and_pushes_nothing(db_session, push_on, apns_mock, frozen, season):
    top = leaders(db_session, season, 100, 90, 80, 70, 60, 50)
    assert held(db_session, frozen) == 0
    assert rank_rows(db_session) == []
    assert [standing(db_session, m).told for m in top[:5]] == [RankStanding.FIRST] + [RankStanding.TOP5] * 4
    assert standing_row(db_session, top[5]) is None  # never in the top 5: no row


def test_a_new_semester_starts_silently(db_session, push_on, apns_mock, frozen, season):
    summer_top = leaders(db_session, season, 100, 90, 80, 70, 60)
    look(db_session, frozen)
    fall = Semesters(
        term=SemesterTerm.FIRST,
        hijri_year=1448,
        academic_year_start=2026,
        start_date=date(2026, 8, 23),
        end_date=date(2026, 12, 17),
        is_public=True,
    )
    db_session.add(fall)
    db_session.flush()
    first_event = make_event(
        db_session,
        start=datetime(2026, 8, 24, 16, 0),
        status=EventsStatus.CLOSED,
        location_type=EventsLocationType.NONE,
    )
    assert first_event.semester_id == fall.id
    newcomers = leaders(db_session, first_event, 40, 30, 20, 15, 12)

    frozen.set(datetime(2026, 8, 21, 9, 0))  # between semesters: Summer has ended, Fall has not started
    assert held(db_session, frozen) == 0

    frozen.set(datetime(2026, 8, 25, 9, 0))  # in Fall 2026: its first look
    assert held(db_session, frozen) == 0
    assert rank_rows(db_session) == []
    assert standing(db_session, newcomers[0]).semester_id == fall.id
    # Summer's top 5 have no Fall points: nothing about them, either way.
    in_fall = select(MemberRankStandings.member_id).where(MemberRankStandings.semester_id == fall.id)
    assert set(db_session.scalars(in_fall).all()).isdisjoint(m.id for m in summer_top)

    climber = app_user(db_session)
    give(db_session, first_event, climber, 50)
    db_session.commit()
    assert held(db_session, frozen) == 1
    [row] = rank_rows(db_session)
    assert (row.kind, row.dedupe_key) == (PushKind.RANK_FIRST, f"rank_first:481:{climber.id}:1")


def test_a_push_the_semester_outlived_is_skipped(db_session, push_on, apns_mock, frozen, season):
    leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    db_session.commit()
    frozen.set(datetime(2026, 8, 20, 19, 0))  # 22:00 on Summer's last day
    look(db_session, frozen)
    give(db_session, season, climber, 45)
    db_session.commit()
    assert held(db_session, frozen) == 1
    frozen.set(datetime(2026, 8, 21, 5, 0))  # 08:00, the day after it ended
    deliver()
    [row] = rank_rows(db_session)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "semester_over")


# ---------------------------------------------------------------- flood guards


def test_a_small_board_says_nothing(db_session, push_on, apns_mock, frozen, season):
    members = []
    for total in (100, 90, 80, 70, 60, 50, 40, 30, 20, 10):
        member = app_user(db_session)
        give(db_session, season, member, total)
        members.append(member)
    db_session.commit()
    assert held(db_session, frozen) == 0
    give(db_session, season, members[-1], 200)
    db_session.commit()
    assert held(db_session, frozen) == 0
    assert rank_rows(db_session) == []
    assert db_session.scalars(select(MemberRankStandings)).all() == []


def test_a_crowded_tie_is_not_news_until_it_breaks(db_session, push_on, apns_mock, frozen, season):
    top = leaders(db_session, season, 100, 90, 80, 70, 60)
    crowd = []
    for _ in range(6):
        member = app_user(db_session)
        give(db_session, season, member, 20)
        crowd.append(member)
    db_session.commit()
    look(db_session, frozen)

    for member in crowd:
        give(db_session, season, member, 40)  # 60: seven members share #5
    db_session.commit()
    assert held(db_session, frozen) == 0
    assert standing(db_session, top[4]).observed == RankStanding.TOP5  # left as it was
    assert all(standing_row(db_session, member) is None for member in crowd)

    give(db_session, season, crowd[0], 5)  # 65: alone at #5, the other six at #6
    db_session.commit()
    assert held(db_session, frozen) == 1
    [row] = rank_rows(db_session)
    assert (row.kind, row.member_id) == (PushKind.RANK_TOP5, crowd[0].id)


def test_a_big_tie_at_first_is_not_news(db_session, push_on, apns_mock, frozen, season):
    """The semester's first event gives every attendee the same points: 25 members share #1."""
    attendees = []
    for _ in range(25):
        member = app_user(db_session)
        give(db_session, season, member, 10)
        attendees.append(member)
    db_session.commit()
    assert held(db_session, frozen) == 0
    # The first look takes the whole tie as known: each attendee is settled at #1.
    rows = db_session.scalars(select(MemberRankStandings)).all()
    assert len(rows) == 25 and {row.told for row in rows} == {RankStanding.FIRST}
    # The tie breaks: #1 kept, #1 lost inside the top 5, the other 23 still a crowded tie. Nothing.
    give(db_session, season, attendees[0], 5)
    give(db_session, season, attendees[1], 3)
    db_session.commit()
    assert held(db_session, frozen) == 0
    assert rank_rows(db_session) == []
    assert standing(db_session, attendees[0]).told == RankStanding.FIRST
    assert standing(db_session, attendees[1]).told == RankStanding.TOP5


def test_a_crowded_tie_at_the_first_look_breaking_later_is_not_news(db_session, push_on, apns_mock, frozen, season):
    """Six members share #4 at launch: in the top 5 before it, so the tie breaking sends none of them anything."""
    leaders(db_session, season, 120, 110, 100)
    crowd = []
    for _ in range(6):
        member = app_user(db_session)
        give(db_session, season, member, 90)
        crowd.append(member)
    db_session.commit()
    assert held(db_session, frozen) == 0
    assert [standing(db_session, member).told for member in crowd] == [RankStanding.TOP5] * 6

    give(db_session, season, crowd[0], 10)  # 100: a shared #3, the other five a shared #5
    db_session.commit()
    assert held(db_session, frozen) == 0
    assert rank_rows(db_session) == []
    assert [standing(db_session, member).told for member in crowd] == [RankStanding.TOP5] * 6


def test_an_event_that_turns_the_top_5_over_is_news(db_session, push_on, apns_mock, frozen, season):
    """Six members pass the whole top 5 at once (two share #5): each is told. The old top 5 were never told in."""
    top = leaders(db_session, season, 100, 90, 80, 70, 60)
    newcomers = []
    for _ in range(6):
        member = app_user(db_session)
        give(db_session, season, member, 30)
        newcomers.append(member)
    db_session.commit()
    look(db_session, frozen)

    for member, extra in zip(newcomers, (100, 95, 90, 85, 80, 80)):
        give(db_session, season, member, extra)  # 130, 125, 120, 115, 110, 110: 6 standings in, 5 out
    db_session.commit()
    assert held(db_session, frozen) == 6
    found = {row.member_id: row.kind for row in rank_rows(db_session)}
    assert found == {newcomers[0].id: PushKind.RANK_FIRST} | {m.id: PushKind.RANK_TOP5 for m in newcomers[1:]}
    assert [standing(db_session, m).told for m in top] == [RankStanding.OUT] * 5


def test_a_catalogue_edit_put_back_within_10_minutes_sends_nothing(db_session, push_on, apns_mock, frozen, season):
    """A catalogue edit puts nine members in the top 5 at once (five share #5) and five out, then is put back."""
    top = leaders(db_session, season, 100, 90, 80, 70, 60)
    shared = attendance_log(db_session, season, points=5)
    group = []
    for extra in (5, 4, 3, 2, 1, 1, 1, 1, 1):
        member = app_user(db_session)
        db_session.add(MembersLogs(member_id=member.id, log_id=shared.id, date=datetime(2026, 7, 10, 16, 30)))
        give(db_session, season, member, extra)
        group.append(member)
    db_session.commit()
    look(db_session, frozen)

    action = db_session.get(Actions, shared.action_id)
    action.points = 200  # PUT /actions/{id}: every log using it is worth 200 now
    db_session.commit()
    assert look(db_session, frozen) == 0
    action.points = 5  # put back, 5 minutes later
    db_session.commit()
    assert look(db_session, frozen, minutes=5) == 0
    assert look(db_session, frozen, minutes=5) == 0
    assert look(db_session, frozen, minutes=5) == 0
    assert rank_rows(db_session) == []
    assert standing(db_session, top[0]).told == RankStanding.FIRST
    assert standing(db_session, group[0]).told == RankStanding.OUT


# ---------------------------------------------------------------- switch, bulk awards, deletion


def test_the_points_switch_turns_them_off(db_session, push_on, apns_mock, frozen, season):
    leaders(db_session, season, 100, 90, 80, 70, 60)
    muted = app_user(db_session, points=False)
    give(db_session, season, muted, 30)
    db_session.commit()
    look(db_session, frozen)
    give(db_session, season, muted, 80)
    db_session.commit()
    assert held(db_session, frozen) == 0
    assert rank_rows(db_session) == []
    assert standing(db_session, muted).told == RankStanding.FIRST


@pytest.fixture
def kicks(monkeypatch):
    """The delivery a route schedules after its commit, recorded instead of run; the test delivers itself."""
    calls: list[int] = []

    async def kick() -> None:
        calls.append(1)

    monkeypatch.setattr(delivery, "kick", kick)
    return calls


def test_a_bulk_award_is_one_push_per_member(admin_client, db_session, push_on, apns_mock, frozen, season, kicks):
    leaders(db_session, season, 100, 90, 80, 70, 60)
    climbers = [app_user(db_session) for _ in range(3)]
    for member in climbers:
        give(db_session, season, member, 30)
    db_session.commit()
    look(db_session, frozen)

    ids = [member.id for member in climbers]
    response = admin_client.post(
        "/custom/members",
        json={
            "event_id": season.id,
            "event_name": "ignored",
            "location_type": "online",
            "start_datetime": "2026-07-10T00:00:00",
            "end_datetime": "2026-07-10T00:00:00",
            "point_deatils": [{"member_ids": ids, "points": 30}, {"member_ids": ids, "points": 15}],
        },
    )
    assert_2xx(response)  # two rows each: 75, all three share #4
    assert held(db_session, frozen) == 3
    assert sorted(row.member_id for row in rank_rows(db_session)) == sorted(ids)
    assert {row.kind for row in rank_rows(db_session)} == {PushKind.RANK_TOP5}
    assert look(db_session, frozen, minutes=5) == 0
    deliver()
    assert {row.payload["en"]["body"] for row in rank_rows(db_session)} == {
        "You're #4 this semester. Keep going, first place is close!"
    }


def test_deleting_an_account_drops_its_standings_and_moves_the_others_up(
    db_session, push_on, apns_mock, frozen, season
):
    top = leaders(db_session, season, 100, 90, 80, 70, 60, 50)
    look(db_session, frozen)
    member_queries.anonymize_member(db_session, top[0])
    db_session.commit()
    assert standing_row(db_session, top[0]) is None
    assert held(db_session, frozen) == 2  # top[1] is #1 now, top[5] #5
    assert {(r.kind, r.member_id) for r in rank_rows(db_session)} == {
        (PushKind.RANK_FIRST, top[1].id),
        (PushKind.RANK_TOP5, top[5].id),
    }


def test_a_withdrawn_push_out_for_delivery_waits_for_the_next_look(db_session, push_on, apns_mock, frozen, season):
    leaders(db_session, season, 100, 90, 80, 70, 60)
    climber = app_user(db_session)
    give(db_session, season, climber, 30)
    db_session.commit()
    look(db_session, frozen)
    frozen.set(datetime(2026, 7, 20, 19, 30))
    mistake = give(db_session, season, climber, 30)  # 60: #5 with top[4], who stays in
    db_session.commit()
    held(db_session, frozen)
    [row] = rank_rows(db_session)
    row.attempts = 1  # another worker claimed it
    db_session.commit()
    frozen.set(datetime(2026, 7, 20, 23, 0))
    take_back(db_session, mistake)
    assert held(db_session, frozen) == 0
    [row] = rank_rows(db_session)
    assert row.status == PushStatus.PENDING  # left to the delivery, which checks it again
    assert standing(db_session, climber).told == RankStanding.TOP5


# ---------------------------------------------------------------- the kinds


def test_the_rank_kinds_answer_to_the_points_switch_and_open_the_leaderboard():
    assert {kind.value: outbox.SWITCH_FOR[kind] for kind in rank.KINDS} == {
        "rank_top5": "points",
        "rank_first": "points",
        "rank_left": "points",
    }
    assert links.LEADERBOARD_URL == "gdgq://leaderboard"
    assert links.is_allowed("gdgq://leaderboard")
    assert not links.is_allowed("gdgq://leaderboard/1")
    assert rank.KINDS <= delivery.LOW_PRIORITY
    assert any(rule.__name__ == "enqueue_rank_changes" for rule in pipeline_sweep.RULES)


@pytest.mark.parametrize("kind", sorted(rank.KINDS), ids=str)
def test_a_switch_turned_off_before_delivery_skips_a_rank_push(db_session, push_on, apns_mock, frozen, kind):
    member = app_user(db_session)
    outbox.enqueue(
        db_session,
        kind,
        [Push(member_id=member.id, dedupe_key=f"{kind.value}:switch:{member.id}", cause={})],
        text=messages.rank_left(),
        url=links.LEADERBOARD_URL,
        thread_id="points",
        expires_at=frozen.now + TWELVE_HOURS,
    )
    set_prefs(db_session, member, points=False)
    db_session.commit()
    deliver()
    [row] = rank_rows(db_session)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "switch_off")


def test_the_texts_isolate_the_numbers_and_count_the_points():
    assert messages.rank_top5(3)["ar"]["body"] == f"صرت رقم {LRI}3{PDI} في ترتيب الفصل. كمّل، الأول قريب!"
    assert messages.rank_first(7)["ar"]["body"] == f"تصدّرت ترتيب الأعضاء هذا الفصل بـ{LRI}7{PDI} نقاط. خلّك فوق!"
    assert messages.rank_first(2)["ar"]["body"] == f"تصدّرت ترتيب الأعضاء هذا الفصل بـ{LRI}2{PDI} نقطتين. خلّك فوق!"
    assert messages.rank_first(1)["en"]["body"] == "You lead the members this semester with 1 point. Stay on top!"


def test_the_board_ranks_as_the_app_does():
    board = rank.Board(
        {
            member_id: rank.Place(rank_, points)
            for member_id, rank_, points in ((1, 1, 50), (2, 2, 40), (3, 2, 40), (4, 4, 30), (5, 5, 20), (6, 6, 10))
        }
    )
    assert [board.standing(m) for m in range(1, 8)] == [
        RankStanding.FIRST,
        RankStanding.TOP5,
        RankStanding.TOP5,
        RankStanding.TOP5,
        RankStanding.TOP5,
        RankStanding.OUT,
        RankStanding.OUT,  # not ranked
    ]
    assert board.crowded == set()


def test_competition_ranks_and_unranked_totals(db_session, season):
    members = [make_member(db_session) for _ in range(5)]
    for member, points in zip(members, (30, 20, 20, 10, 0)):
        if points:
            give(db_session, season, member, points)
    discounted = make_member(db_session)
    entry = give(db_session, season, discounted, 5)
    db_session.add(Modifications(log_id=entry.log_id, type=ModificationsType.DISCOUNT, value=10))  # -5
    db_session.flush()
    board = rank.Board.of(db_session, season.semester_id)
    assert [board.places.get(m.id) for m in members] == [
        rank.Place(1, 30),
        rank.Place(2, 20),
        rank.Place(2, 20),
        rank.Place(4, 10),
        None,
    ]
    assert discounted.id not in board.places
