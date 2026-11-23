from sqlalchemy import select

from app.DB.schema import SoobDrawEntries

PATH = "/soob-draw/entry"


def test_draw_requires_member(client):
    assert client.get(PATH).status_code == 403
    assert client.post(PATH, json={"x": 0.68, "y": 0.74}).status_code == 403


def test_record_one_entry_per_member_and_report_it_on_return(club):
    first, second = club.person(), club.person()
    client = club.sign_in(first)
    status = client.get(PATH)
    assert status.status_code == 200
    assert status.json()["entered"] is False
    box = status.json()["hitbox"]
    hit = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}

    assert client.post(PATH, json={"x": 0, "y": 0}).status_code == 422
    assert client.get(PATH).json()["entered"] is False
    for _ in range(2):
        response = client.post(PATH, json=hit)
        assert response.status_code == 200, response.text
        assert response.json() == {"entered": True}
    assert client.get(PATH).json()["entered"] is True
    entries = club.session.scalars(select(SoobDrawEntries)).all()
    assert [entry.member_id for entry in entries] == [first.id]

    client = club.sign_in(second)
    assert client.get(PATH).json()["entered"] is False
    assert client.post(PATH, json=hit).status_code == 200
    assert client.get(PATH).json()["entered"] is True
