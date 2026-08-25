"""Guard against `response_model` silently dropping fields.

FastAPI filters a response down to the fields its `response_model` declares.
A key the model forgets is not an error - it just vanishes from the payload,
which is how an API quietly breaks a client that is not in this repository
(the leaderboard app consumes `/wallet/*`, `/events`, `/points` and friends).

So: parse every route handler, collect the keys of every dict literal it
returns, and assert the declared model can represent all of them.
"""

import ast
import pathlib
from typing import Any

import pytest
from fastapi.routing import APIRoute

from app.main import app

ROUTERS = pathlib.Path(__file__).resolve().parent.parent / "app" / "routers"


def dict_keys_returned_by(func_name: str) -> set[str] | None:
    """Literal keys of every `return {...}` in a top-level handler, or None."""
    for path in sorted(ROUTERS.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != func_name:
                continue
            keys: set[str] = set()
            found = False
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Return) or not isinstance(inner.value, ast.Dict):
                    continue
                literal: set[str] = set()
                for key in inner.value.keys:
                    if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                        literal.clear()
                        break
                    literal.add(key.value)
                if literal:
                    keys |= literal
                    found = True
            return keys if found else None
    return None


def _response_class_name(route: APIRoute) -> str:
    """FastAPI wraps an unset response_class in a DefaultPlaceholder."""
    rc: Any = route.response_class
    rc = getattr(rc, "value", rc)
    return rc.__name__ if isinstance(rc, type) else type(rc).__name__


def routes_with_models():
    seen = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.response_model is None:
            continue
        fields = getattr(route.response_model, "model_fields", None)
        if fields is None:  # list[...] / dict and other non-model annotations
            continue
        if route.name in seen:
            continue
        seen.add(route.name)
        yield route


@pytest.mark.parametrize("route", list(routes_with_models()), ids=lambda r: r.name)
def test_response_model_declares_every_key_the_handler_returns(route):
    returned = dict_keys_returned_by(route.name)
    if returned is None:
        pytest.skip(f"{route.name} does not return a dict literal")

    model: Any = route.response_model
    declared = set(model.model_fields)
    aliases = {f.alias for f in model.model_fields.values() if f.alias}
    dropped = returned - declared - aliases

    assert not dropped, (
        f"{route.methods} {route.path} returns {sorted(dropped)}, which "
        f"{model.__name__} does not declare - those keys would be "
        f"silently stripped from the response"
    )


def test_every_route_declares_a_response_shape():
    """New routes must say what they return.

    The three exemptions return a null body today and are consumed by the member
    app, so changing their shape is a separate, deliberate decision. Routes kept
    out of the schema (the `/` redirect) are not part of the contract.
    """
    exempt = {"mark_attendance", "print_pool_status", "manual_run_google_form_submissions"}
    undeclared = sorted(
        f"{sorted(r.methods)[0]} {r.path}"
        for r in app.routes
        if isinstance(r, APIRoute)
        and r.response_model is None
        and r.include_in_schema
        and r.name not in exempt
        and _response_class_name(r) == "JSONResponse"
    )
    assert not undeclared, f"routes without a response_model: {undeclared}"


def test_submission_response_covers_every_submissions_column():
    """POST /submissions/{form_id} returns an ORM row, which the dict-literal
    scan above cannot see. Check it against the table instead."""
    from sqlalchemy import inspect

    from app.DB.schema import Submissions
    from app.routers.responses import SubmissionResponse

    columns = {c.key for c in inspect(Submissions).mapper.column_attrs}
    declared = set(SubmissionResponse.model_fields)
    assert not columns - declared, (
        f"SubmissionResponse omits {sorted(columns - declared)}, which would be dropped from the response"
    )
