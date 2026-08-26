"""Async route handlers must not do blocking work on the event loop.

FastAPI runs a plain `def` handler in a threadpool, so synchronous database and
S3 calls there are fine. An `async def` handler runs *on* the event loop, where
the same call stalls every other request that worker is serving.

This codebase had six handlers declared `async def` while talking to a
synchronous SQLAlchemy session - strictly worse than not using async at all.
"""

import ast
import pathlib

import pytest

ROUTERS = pathlib.Path(__file__).resolve().parent.parent / "app" / "routers"

# Substrings that mean "this statement blocks the calling thread".
BLOCKING_CALLS = ("session.", "_queries.", "get_from_address(", "put_object", "db_session(")

# The escape hatch: work handed to run_in_threadpool is off the loop by definition.
OFFLOAD = "run_in_threadpool"


def is_route(fn: ast.AST) -> bool:
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for d in fn.decorator_list:
        node = d.func if isinstance(d, ast.Call) else d
        if isinstance(node, ast.Attribute) and getattr(node.value, "id", None) == "router":
            return True
    return False


def async_handlers():
    for path in sorted(ROUTERS.glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.AsyncFunctionDef) and is_route(node):
                yield path.name, node


def inline_statements(fn: ast.AST) -> list[ast.stmt]:
    """Statements in the handler's own body, skipping nested function definitions."""
    found: list[ast.stmt] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.stmt):
                found.append(child)
            walk(child)

    walk(fn)
    return found


@pytest.mark.parametrize(
    "handler", list(async_handlers()), ids=lambda h: f"{h[0]}::{h[1].name}" if isinstance(h, tuple) else str(h)
)
def test_async_handler_does_not_block_the_event_loop(handler):
    filename, fn = handler
    offenders = []
    for stmt in inline_statements(fn):
        source = ast.unparse(stmt)
        if OFFLOAD in source:
            continue  # explicitly offloaded
        for call in BLOCKING_CALLS:
            if call in source:
                offenders.append((stmt.lineno, call, source.splitlines()[0][:70]))
                break

    assert not offenders, (
        f"{filename}::{fn.name} is `async def` but runs blocking work on the event loop:\n"
        + "\n".join(f"  line {ln}: {call} in `{src}`" for ln, call, src in offenders)
        + "\n\nEither make the handler `def`, or wrap the blocking part in run_in_threadpool()."
    )


def test_handlers_with_no_await_are_not_async():
    """`async def` with nothing to await only moves work onto the event loop."""
    pointless = []
    for filename, fn in async_handlers():
        awaits = [n for n in inline_statements(fn) if "await " in ast.unparse(n)]
        if not awaits:
            pointless.append(f"{filename}::{fn.name}")
    assert not pointless, f"async handlers with no await in their own body: {pointless}"
