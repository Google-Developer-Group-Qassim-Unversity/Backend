"""Every table's id is a UUID (CLAUDE.md, "Ids are UUIDs"), except the older tables listed here.

The list only shrinks. A table that needs an id gets a UUID, never an
auto-increment integer.
"""

import uuid

from sqlalchemy.dialects.mysql import INTEGER

from app.DB.ids import new_id
from app.DB.schema import Base

# Integer ids from before the rule. Too much depends on them (the leaderboard app among it) to switch.
LEGACY_INTEGER_IDS = {
    "actions",
    "departments",
    "departments_logs",
    "email_jobs",
    "email_logs",
    "email_templates",
    "events",
    "form_access_grants",
    "form_sync_jobs",
    "forms",
    "logs",
    "member_profiles",
    "members",
    "members_logs",
    "modifications",
    "submissions",
}


def test_no_new_table_has_an_integer_id():
    integer_ids = {
        table.name
        for table in Base.metadata.tables.values()
        if "id" in table.columns and table.columns["id"].primary_key and isinstance(table.columns["id"].type, INTEGER)
    }
    assert integer_ids - LEGACY_INTEGER_IDS == set(), "new tables get UUID ids, see CLAUDE.md"
    assert LEGACY_INTEGER_IDS - integer_ids == set(), "a legacy table switched to UUIDs: take it off the list"


def test_new_ids_sort_in_the_order_they_were_made():
    ids = [new_id() for _ in range(2000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert all(uuid.UUID(i).version == 7 for i in ids)
