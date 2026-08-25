"""Settings behaviour that the rest of the app depends on."""

import pathlib
import re

import pytest

from app.config import MissingSettingError, Settings, config, env_or_except, reload_settings

DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "ENVIRONMENT_VARIABLES.md"


def test_every_setting_is_documented():
    """The reference table is written by hand from the model; keep them in sync."""
    documented = set(re.findall(r"^\| `([A-Z][A-Z_0-9]+)`", DOC.read_text(), re.M))
    declared = set(Settings.model_fields)
    assert not declared - documented, f"undocumented settings: {sorted(declared - documented)}"
    assert not documented - declared, f"documented but not declared: {sorted(documented - declared)}"


def test_settings_are_lazy(monkeypatch):
    """Importing the app must not require a database.

    The test suite relies on this: it imports app.main at conftest module level,
    before the MySQL container exists.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reload_settings()
    Settings()  # must not raise


def test_missing_required_setting_names_itself(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reload_settings()
    with pytest.raises(MissingSettingError, match="DATABASE_URL"):
        _ = config.DATABASE_URL


def test_empty_string_counts_as_missing(monkeypatch):
    monkeypatch.setenv("SES_FROM_ADDRESS", "")
    reload_settings()
    with pytest.raises(MissingSettingError):
        _ = config.SES_FROM_ADDRESS


def test_env_or_except_honours_a_default(monkeypatch):
    monkeypatch.delenv("SES_FROM_ADDRESS", raising=False)
    reload_settings()
    assert env_or_except("SES_FROM_ADDRESS", "fallback@example.com") == "fallback@example.com"


def test_is_dev_reads_env(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    reload_settings()
    assert config.is_dev is True

    monkeypatch.setenv("ENV", "Production")
    reload_settings()
    assert config.is_dev is False


def test_dotenv_precedence_is_unchanged():
    """`.env.local` overrides real environment variables here.

    pydantic-settings does the opposite by default, so config.py keeps the
    explicit load_dotenv(override=True) call rather than using `env_file`.
    """
    source = (pathlib.Path(__file__).resolve().parent.parent / "app" / "config.py").read_text()
    assert 'load_dotenv(".env.local", override=True)' in source
    # checked on the model, not by grepping the file, so the docstring that
    # explains this choice does not fail the test that enforces it
    assert Settings.model_config.get("env_file") is None
