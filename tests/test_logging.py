"""Cover the logging setup that replaced app/routers/logging.py."""

import ast
import logging
import pathlib

import pytest

from app.logging_config import RequestContextFilter, configure_logging, request_id_var
from app.middleware import REQUEST_ID_HEADER

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


def make_record() -> logging.LogRecord:
    return logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)


def test_filter_stamps_the_current_request_id():
    token = request_id_var.set("abc123def456")
    try:
        record = make_record()
        assert RequestContextFilter().filter(record) is True
        # the filter adds the attribute; LogRecord does not declare it
        assert getattr(record, "request_id") == "abc123def456"
    finally:
        request_id_var.reset(token)


def test_filter_falls_back_outside_a_request():
    record = make_record()
    RequestContextFilter().filter(record)
    assert getattr(record, "request_id") == "-"


def test_configure_logging_is_idempotent():
    """The lifespan calls it; a second call must not double every line."""
    root = logging.getLogger()
    original = root.handlers[:]
    original_level = root.level
    try:
        configure_logging("INFO")
        after_first = len(root.handlers)
        configure_logging("INFO")
        assert len(root.handlers) == after_first == 1
    finally:
        root.handlers[:] = original
        root.setLevel(original_level)


def test_request_gets_an_id_echoed_in_the_response(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]


def test_caller_supplied_request_id_is_honoured(client):
    """Lets a caller correlate its own trace with ours."""
    response = client.get("/health", headers={REQUEST_ID_HEADER: "trace-from-caller"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-from-caller"


def test_each_request_logs_one_summary_line(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.middleware"):
        client.get("/health")

    summaries = [r.getMessage() for r in caplog.records if r.name == "app.middleware"]
    assert len(summaries) == 1
    assert summaries[0].startswith("GET /health -> 200 in ")


def test_route_logging_goes_through_stdlib_logging(client, caplog):
    """A route's own log lines are attributed to its module, so they can be
    filtered per router in production."""
    with caplog.at_level(logging.INFO):
        client.get("/events/")

    assert any(r.name == "app.routers.events" for r in caplog.records)


def python_files():
    return sorted(p for p in APP_DIR.rglob("*.py") if "__pycache__" not in str(p))


@pytest.mark.parametrize("path", python_files(), ids=lambda p: p.name)
def test_no_bespoke_logging_helpers_remain(path):
    source = path.read_text()
    for banned in ("write_log", "LogFile", "app.routers.logging"):
        assert banned not in source, f"{path} still references {banned}"


@pytest.mark.parametrize("path", python_files(), ids=lambda p: p.name)
def test_no_print_calls_remain(path):
    """AST-based, so the word "print" inside a string does not trip it."""
    calls = [
        node.lineno
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    ]
    assert not calls, f"{path} still calls print() at line(s) {calls}"
