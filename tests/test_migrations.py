"""The deploy runs `alembic upgrade head` against production before restarting
the app, so these catch the two ways that step goes wrong before a PR merges."""

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_single_head():
    """Two PRs that each add a migration on the same parent leave two heads, and
    `alembic upgrade head` refuses to run - the deploy would stop there."""
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert len(heads) == 1, f"multiple alembic heads {heads}; add a merge migration"


def test_models_match_migrations(engine):
    """A model change without a migration deploys code that queries columns the
    production schema does not have - `Unknown column` on every request."""
    command.check(Config("alembic.ini"))
