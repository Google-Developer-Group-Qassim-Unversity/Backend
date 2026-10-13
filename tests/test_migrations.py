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


def test_the_content_links_downgrade_round_trips(downgrade_drill_database_url):
    """Not just the upgrade path: the deploy never downgrades, but every
    migration must be able to downgrade cleanly wherever it already ran.

    The drill runs in its own scratch schema (`downgrade_drill_database_url`),
    never on the seeded session database: a lossy downgrade of a future
    revision would otherwise strip the suite's data next to it. It targets the
    head's own `down_revision` (read from the script, so the name never
    drifts), which keeps exercising only the newest revisions in every future
    run - the event FK's only backing index is the composite unique one, so
    this is still where error 1553 ("cannot drop index, needed in a foreign
    key constraint") would come back from.
    """
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", downgrade_drill_database_url)
    script = ScriptDirectory.from_config(config)
    head = script.get_heads()[0]
    target = script.get_revision(head).down_revision
    # The drill downgrades exactly one revision. A merge migration's parent
    # would be a tuple of ids - not expressible as a single `downgrade` target;
    # if a merge ever sits at the head, give the drill an explicit name again.
    assert isinstance(target, str), f"head [{head}] is not one revision after its parent [{target}]"

    command.upgrade(config, "head")
    command.downgrade(config, target)
    command.upgrade(config, "head")
    # The models are rebuilt exactly as they were; parity confirms it.
    command.check(config)
