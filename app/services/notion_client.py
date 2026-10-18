"""The few Notion API calls the design team's board needs.

One internal integration token, set up once by hand (Notion: Events pipeline
→ Design's Notion board has the steps).
Every call is a fresh ``httpx.Client`` so the test suite's transport recorder
sees it, like the other sync gateway calls. Nothing here knows about requests
or briefs; ``app/services/pipeline_notion.py`` builds the payloads.

Notion answers 429 with ``Retry-After`` when it is called faster than about
three times a second; a sync makes a handful of calls, so one wait and retry
is enough. Any other error is raised as :class:`NotionError` with Notion's own
message, which the sync stores on the task for the request page to show.
"""

import logging
import time
from typing import Any

import httpx

from app.config import config

logger = logging.getLogger(__name__)

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
TIMEOUT = 20.0
# Notion's limits: blocks per append call, and characters per rich-text piece.
MAX_CHILDREN = 100
MAX_TEXT = 2000


class NotionError(RuntimeError):
    """Notion refused a call. ``str()`` is Notion's message, short enough to show."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"Notion {status} {code}: {message}")
        self.status = status
        self.code = code


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": VERSION,
        "Content-Type": "application/json",
    }


def call(method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict[str, Any]:
    """One API call, retried once after a 429."""
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.request(method, f"{API}{path}", headers=_headers(), json=body, params=params)
        if response.status_code == 429:
            wait = min(float(response.headers.get("Retry-After", "1")), 5.0)
            logger.info("Notion rate limit on %s %s; waiting %.1fs", method, path, wait)
            time.sleep(wait)
            response = client.request(method, f"{API}{path}", headers=_headers(), json=body, params=params)
    if response.status_code >= 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        raise NotionError(
            response.status_code, payload.get("code", "error"), payload.get("message", response.text[:300])
        )
    return response.json()


# --------------------------------------------------------------------------- pages


def create_page(database_id: str, properties: dict, children: list[dict], icon: str | None = None) -> dict:
    body: dict[str, Any] = {"parent": {"database_id": database_id}, "properties": properties}
    if children:
        body["children"] = children[:MAX_CHILDREN]
    if icon:
        body["icon"] = {"type": "emoji", "emoji": icon}
    page = call("POST", "/pages", body)
    for chunk in _chunks(children[MAX_CHILDREN:]):
        append_children(page["id"], chunk)
    return page


def update_page(page_id: str, properties: dict, icon: str | None = None) -> dict:
    body: dict[str, Any] = {"properties": properties}
    if icon:
        body["icon"] = {"type": "emoji", "emoji": icon}
    return call("PATCH", f"/pages/{page_id}", body)


def trash_page(page_id: str) -> dict:
    return call("PATCH", f"/pages/{page_id}", {"in_trash": True})


def update_database(database_id: str, title: str, description: str, properties: dict) -> dict:
    """Set the board's columns. Properties not listed are left alone, so the team's own columns survive."""
    return call(
        "PATCH",
        f"/databases/{database_id}",
        {
            "title": [{"type": "text", "text": {"content": title}}],
            "description": [{"type": "text", "text": {"content": description}}],
            "properties": properties,
        },
    )


# --------------------------------------------------------------------------- blocks


def list_children(block_id: str) -> list[dict]:
    """Every direct child of a block or page, following pagination."""
    results: list[dict] = []
    cursor = None
    while True:
        params = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        page = call("GET", f"/blocks/{block_id}/children", params=params)
        results.extend(page.get("results", []))
        if not page.get("has_more"):
            return results
        cursor = page.get("next_cursor")


def delete_block(block_id: str) -> None:
    call("DELETE", f"/blocks/{block_id}")


def append_children(block_id: str, children: list[dict]) -> None:
    for chunk in _chunks(children):
        call("PATCH", f"/blocks/{block_id}/children", {"children": chunk})


def _chunks(children: list[dict]) -> list[list[dict]]:
    return [children[i : i + MAX_CHILDREN] for i in range(0, len(children), MAX_CHILDREN)]


# --------------------------------------------------------------------------- rich text helpers


def text(content: str, *, bold: bool = False, link: str | None = None, code: bool = False) -> list[dict]:
    """``content`` as rich text, split so no piece passes Notion's 2000-character limit."""
    pieces = []
    for start in range(0, max(len(content), 1), MAX_TEXT):
        piece: dict[str, Any] = {"type": "text", "text": {"content": content[start : start + MAX_TEXT]}}
        if link:
            piece["text"]["link"] = {"url": link}
        if bold or code:
            piece["annotations"] = {"bold": bold, "code": code}
        pieces.append(piece)
    return pieces


def rich(*parts: list[dict]) -> list[dict]:
    """Several ``text()`` runs as one rich-text array."""
    return [piece for part in parts for piece in part]
