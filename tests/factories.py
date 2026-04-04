from app.DB.schema import EventsLocationType
from app.routers.models import Events_model
from datetime import datetime

# CAUTION: Don't update default unless you know what you're doing
# a lot of tests assume these default values and changing them might break the tests
def make_event(**overrides):
    defaults = {
        "name": "my event",
        "description": "my description",
        "location_type": EventsLocationType.ONLINE,
        "location": "space",
        "start_datetime": datetime.fromisoformat("2026-03-01T00:00:00"),
        "end_datetime": datetime.fromisoformat("2026-03-01T00:00:00"),
        "status": "draft",
        "image_url": None,
        "is_official": 0,
        "created_at": None,
    }
    defaults.update(overrides)
    return Events_model(**defaults).model_dump(mode="json")


def make_create_event_payload(**overrides):
    event = overrides.pop("event", None) or make_event()
    defaults = {
        "event": event,
        "form_type": "none",
        "department_action_id": 1,
        "member_action_id": 2,
        "department_id": 1,
    }
    defaults.update(overrides)
    return defaults