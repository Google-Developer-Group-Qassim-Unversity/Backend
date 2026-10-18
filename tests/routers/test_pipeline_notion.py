"""Design's card on the team's Notion board: created on submit, edited in place after, never in the way.

A route queues the sync as a background task, which writes the page id back in
a session of its own. Under this suite every session shares one connection
inside one transaction, and the route's session, closed after the background
task, rolls its trailing savepoint back - taking the background task's row
update with it (not its Notion calls, which the recorder keeps). So each step
here is followed by ``persist``, the same sync run from the test, whose write
survives. Production sessions have their own connections and no such problem.
"""

import pytest

from app.config import reload_settings
from app.services import pipeline_notion
from app.services.pipeline_sweep import sweep_once
from tests.outbound import NOTION_PAGE_ID, NOTION_PAGE_URL
from tests.pipeline_support import book_complete, finish, submit, upload_poster

DATABASE_ID = "db-design"


@pytest.fixture
def board(monkeypatch):
    """The board is configured; Notion itself is the transport recorder's canned answers."""
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
    monkeypatch.setenv("NOTION_DESIGN_DATABASE_ID", DATABASE_ID)
    reload_settings()
    pipeline_notion._title_property.clear()
    yield
    pipeline_notion._title_property.clear()


@pytest.fixture
def world(pipeline):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    ai = pipeline.department("AI")
    people = {
        "ai": pipeline.officer(ai),
        "design": pipeline.officer(design, name="Designer"),
        "logistics": pipeline.officer(logistics, name="Logistics"),
    }
    pipeline.sign_in(people["ai"])
    return {"ai": ai, "people": people}


def notion_calls(pipeline, method: str, path_prefix: str):
    return [
        c
        for c in pipeline.outbound.calls
        if c.url.host == "api.notion.com" and c.method == method and c.path.startswith(path_prefix)
    ]


def page_edits(pipeline):
    return notion_calls(pipeline, "PATCH", f"/v1/pages/{NOTION_PAGE_ID}")


def design_task(pipeline, request_id):
    tasks = pipeline.client.get(f"/pipeline/requests/{request_id}").json()["tasks"]
    return next(t for t in tasks if t["team"] == "design")


def persist(request_id):
    """The sync again, from the test, so its row update survives (module docstring)."""
    assert pipeline_notion.sync_request(request_id) is True


def test_submit_creates_the_card_with_the_brief(pipeline, board, world):
    request_id = book_complete(pipeline, world["ai"])
    assert submit(pipeline, request_id).status_code == 200

    # The board's columns go first (read the title column, push the rest), once; then the page.
    assert [c.method for c in pipeline.outbound.to(f"/v1/databases/{DATABASE_ID}")] == ["GET", "PATCH"]
    created = pipeline.outbound.to("/v1/pages")[0].json
    assert created["parent"] == {"database_id": DATABASE_ID}
    props = created["properties"]
    assert props["Name"]["title"][0]["text"]["content"] == "(testing) Intro to ML"
    assert props["Status"]["select"]["name"] == "New"
    assert props["Department"]["select"]["name"] == "AI"
    assert props["Design type"]["select"]["name"] == "Promotional image"
    assert props["Content"]["select"]["name"] == "Final"
    assert props["Event date"]["date"] == {"start": "2026-07-20", "end": "2026-07-21"}
    assert props["Where"]["select"]["name"] == "Hybrid"
    assert props["Requested by"]["rich_text"][0]["text"]["content"].startswith("Leader")
    assert props["Request"]["url"].endswith(f"/pipeline/requests/{request_id}")
    assert "Designer" not in props  # theirs
    # The body is one synced block holding the brief.
    [block] = created["children"]
    assert block["type"] == "synced_block"
    body = " ".join(
        piece["text"]["content"]
        for child in block["synced_block"]["children"]
        for piece in child[child["type"]].get("rich_text", [])
    )
    assert "A robot" in body and "Join us" in body and "Leader" in body

    persist(request_id)
    task = design_task(pipeline, request_id)
    assert task["notion_url"] == NOTION_PAGE_URL
    assert task["notion_pending"] is False


def test_return_and_resubmit_edit_the_same_card(pipeline, board, world):
    request_id = book_complete(pipeline, world["ai"])
    submit(pipeline, request_id)
    persist(request_id)
    pages_made = len(pipeline.outbound.to("/v1/pages"))
    url = f"/pipeline/requests/{request_id}"

    pipeline.sign_in(world["people"]["design"])
    assert pipeline.client.post(f"{url}/return", json={"notes": "Bigger logo"}).status_code == 200
    pipeline.sign_in(world["people"]["ai"])
    assert pipeline.client.post(f"{url}/resubmit").status_code == 200

    assert len(pipeline.outbound.to("/v1/pages")) == pages_made, "a resubmission edits the card, never makes another"
    assert [e.json["properties"]["Status"]["select"]["name"] for e in page_edits(pipeline)] == [
        "Returned to team",
        "New",
    ]
    # Each edit replaces the sync's own synced block and leaves the team's paragraph alone.
    deleted = [c.path for c in notion_calls(pipeline, "DELETE", "/v1/blocks/")]
    assert deleted == ["/v1/blocks/block-ours", "/v1/blocks/block-ours"]
    assert len(notion_calls(pipeline, "PATCH", f"/v1/blocks/{NOTION_PAGE_ID}/children")) == 2


def test_the_poster_and_design_done_follow_without_resetting_status(pipeline, board, world):
    request_id = book_complete(pipeline, world["ai"])
    submit(pipeline, request_id)
    persist(request_id)
    pipeline.sign_in(world["people"]["design"])

    assert upload_poster(pipeline, request_id).status_code == 200
    after_poster = page_edits(pipeline)[-1].json["properties"]
    assert after_poster["Poster"]["files"][0]["external"]["url"].startswith("https://cdn.example.com/event-images/")
    assert "Status" not in after_poster, "a designer's 'In progress' survives a poster upload"

    assert finish(pipeline, request_id, "design").status_code == 200
    assert page_edits(pipeline)[-1].json["properties"]["Status"]["select"]["name"] == "Done"


def test_a_cancelled_request_closes_its_card(pipeline, board, world):
    request_id = book_complete(pipeline, world["ai"])
    submit(pipeline, request_id)
    persist(request_id)
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)
    assert pipeline.client.post(f"/pipeline/requests/{request_id}/cancel").status_code == 200
    assert page_edits(pipeline)[-1].json["properties"]["Status"]["select"]["name"] == "Cancelled"

    assert pipeline.client.delete(f"/pipeline/requests/{request_id}").status_code == 200
    assert page_edits(pipeline)[-1].json == {"in_trash": True}


def test_notion_down_never_blocks_the_step_and_the_sweep_retries(pipeline, board, world):
    pipeline.outbound.stub(
        "/v1/pages", status_code=503, json={"code": "service_unavailable", "message": "Notion is down"}
    )
    request_id = book_complete(pipeline, world["ai"])
    assert submit(pipeline, request_id).status_code == 200
    assert pipeline_notion.sync_request(request_id) is False

    task = design_task(pipeline, request_id)
    assert task["notion_url"] is None
    assert task["notion_pending"] is True
    assert task["notion_error"] is None, "the error is for super admins"
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)
    assert "Notion is down" in design_task(pipeline, request_id)["notion_error"]

    pipeline.outbound._stubs.pop("/v1/pages")
    result = sweep_once()
    assert result is not None and result.notion_synced == 1
    task = design_task(pipeline, request_id)
    assert task["notion_url"] == NOTION_PAGE_URL
    assert task["notion_pending"] is False and task["notion_error"] is None
    again = sweep_once()
    assert again is not None and again.notion_synced == 0  # nothing left to retry


def test_nothing_is_sent_without_the_settings(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    assert submit(pipeline, request_id).status_code == 200
    assert not [c for c in pipeline.outbound.calls if c.url.host == "api.notion.com"]
    assert design_task(pipeline, request_id)["notion_pending"] is False
    assert pipeline_notion.sync_request(request_id) is False


def test_no_card_for_a_request_design_already_finished(pipeline, world, monkeypatch):
    """Switching the board on later must not flood it with cards for requests Design is done with."""
    request_id = book_complete(pipeline, world["ai"])
    submit(pipeline, request_id)
    pipeline.sign_in(world["people"]["design"])
    assert finish(pipeline, request_id, "design").status_code == 200

    monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
    monkeypatch.setenv("NOTION_DESIGN_DATABASE_ID", DATABASE_ID)
    reload_settings()
    pipeline_notion._title_property.clear()
    result = sweep_once()
    assert result is not None and result.notion_synced == 1
    assert pipeline.outbound.to("/v1/pages") == []
    assert design_task(pipeline, request_id)["notion_pending"] is False
