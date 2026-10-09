"""Fixtures shared by the router tests."""

import pytest

from app.config import config
from app.main import app
from tests.pipeline_support import pipeline  # noqa: F401
from tests.r2_support import fake_r2, r2_env  # noqa: F401


@pytest.fixture
def club(db_session, client):
    from tests.access_support import Club

    yield Club(db_session, client)
    app.dependency_overrides.pop(config.CLERK_GUARD, None)
    app.dependency_overrides.pop(config.CLERK_GUARD_optional, None)
