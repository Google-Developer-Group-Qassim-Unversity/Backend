"""The booking-blocks migration groups the days already closed into blocks, and its downgrade gives them back."""

from datetime import date

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.DB.main import _build_connect_args
from tests.conftest import _drill_server_root

BEFORE = "b8d3f1e6a2c4"
BLOCKS = "b4d8e1f6a2c9"


def test_closed_days_become_blocks_and_come_back(downgrade_drill_database_url, monkeypatch):
    # The scratch schema belongs to the server's root account, and alembic/env.py
    # takes DATABASE_URL over the config, so point both at it.
    url = (
        make_url(_drill_server_root["server_url"])
        .set(database=make_url(downgrade_drill_database_url).database)
        .render_as_string(hide_password=False)
    )
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, BEFORE)
    engine = create_engine(url, connect_args=_build_connect_args(url))
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO members (id, name, public_name, gender) VALUES (:id, :name, :name, 'Male')"),
                [{"id": 1, "name": "Lama"}, {"id": 2, "name": "Saad"}],
            )
            conn.execute(
                text("INSERT INTO booking_bans (date, reason, banned_by) VALUES (:d, :r, :by)"),
                [
                    # One run: consecutive days, same reason, same member.
                    {"d": date(2026, 3, 1), "r": "Break", "by": 1},
                    {"d": date(2026, 3, 2), "r": "Break", "by": 1},
                    {"d": date(2026, 3, 3), "r": "Break", "by": 1},
                    # A gap starts a new block, even with the same reason.
                    {"d": date(2026, 3, 5), "r": "Break", "by": 1},
                    # So does another member, or another reason.
                    {"d": date(2026, 3, 6), "r": "Break", "by": 2},
                    {"d": date(2026, 3, 7), "r": "Exams", "by": 2},
                    # A day closed without a reason gets one.
                    {"d": date(2026, 3, 9), "r": None, "by": 2},
                ],
            )

        command.upgrade(config, BLOCKS)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT b.date, k.id, k.reason, k.blocked_by FROM booking_bans b "
                    "JOIN booking_blocks k ON k.id = b.block_id ORDER BY b.date"
                )
            ).all()
        blocks = {}
        for day, block_id, reason, by in rows:
            blocks.setdefault(block_id, (reason, by, []))[2].append(day.day)
        assert sorted(blocks.values(), key=lambda b: b[2][0]) == [
            ("Break", 1, [1, 2, 3]),
            ("Break", 1, [5]),
            ("Break", 2, [6]),
            ("Exams", 2, [7]),
            ("No reason given", 2, [9]),
        ]

        command.downgrade(config, BEFORE)
        with engine.connect() as conn:
            back = conn.execute(text("SELECT date, reason, banned_by FROM booking_bans ORDER BY date")).all()
        assert [(d.day, r, by) for d, r, by in back] == [
            (1, "Break", 1),
            (2, "Break", 1),
            (3, "Break", 1),
            (5, "Break", 1),
            (6, "Break", 2),
            (7, "Exams", 2),
            (9, "No reason given", 2),
        ]
    finally:
        engine.dispose()
