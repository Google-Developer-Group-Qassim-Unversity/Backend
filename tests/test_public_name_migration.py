"""Exercise the real migration's data backfill against pre-feature records."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


def test_backfill_ignores_wallet_names_and_formats_names_in_batches(monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/750018c84549_add_public_member_name.py"
    spec = importlib.util.spec_from_file_location("public_name_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # SQLite keeps this data-migration regression isolated from the shared MySQL
    # schema. The normal migration check verifies the final NOT NULL on MySQL.
    with create_engine("sqlite://", poolclass=NullPool).begin() as connection:
        connection.connection.driver_connection.create_collation(
            "utf8mb4_0900_ai_ci", lambda left, right: (left > right) - (left < right)
        )
        connection.execute(text("CREATE TABLE members (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"))
        connection.execute(text("CREATE TABLE member_profiles (member_id INTEGER, custom_name TEXT)"))
        connection.execute(
            text("INSERT INTO members (id, name) VALUES (:id, :name)"),
            [{"id": i, "name": "Ahmed Mohammed Hassan"} for i in range(1, 1003)]
            + [{"id": 1003, "name": "بدر خالد الدخيل الله"}, {"id": 1004, "name": "  "}],
        )
        connection.execute(
            text("INSERT INTO member_profiles VALUES (:id, :name)"),
            [{"id": 1, "name": "  Existing Public Alias  "}, {"id": 2, "name": "  "}],
        )
        operations = Operations(MigrationContext.configure(connection))
        constraints = []
        monkeypatch.setattr(
            migration,
            "op",
            SimpleNamespace(
                add_column=operations.add_column,
                get_bind=lambda: connection,
                alter_column=lambda *args, **kwargs: constraints.append(kwargs),
            ),
        )
        migration.upgrade()
        names = dict(connection.execute(text("SELECT id, public_name FROM members")).all())
        assert names[1] == names[2] == names[1002] == "Ahmed Hassan"
        assert names[1003] == "بدر الدخيل الله"
        assert names[1004] == "Member"
        assert len(names) == 1004
        assert constraints[0]["nullable"] is False
        assert (
            connection.execute(text("SELECT custom_name FROM member_profiles WHERE member_id = 1")).scalar()
            == "  Existing Public Alias  "
        )
