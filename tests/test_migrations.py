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


def test_the_content_links_downgrade_round_trips(engine, capsys):
    """Not just the upgrade path: the deploy never downgrades, but an amended
    revision must downgrade cleanly wherever it already ran. The event FK's
    only backing index is the composite unique one, so this is where error
    1553 ("cannot drop index, needed in a foreign key constraint") would come
    back from - the revision drops the constraints before its indexes."""
    config = Config("alembic.ini")
    with capsys.disabled():
        target = getattr(engine.url, "database", str(engine.url))
        print(f"[downgrade drill] scratch engine target: {target}")

    command.downgrade(config, "750018c84549")
    command.upgrade(config, "head")
    # The models are rebuilt exactly as they were; parity confirms it.
    command.check(config)
