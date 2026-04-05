def make_event(**overrides):
    defaults = {
        "name": "my event",
        "description": "my description",
        "location_type": "online",
        "location": "space",
        "start_datetime": "2026-03-01T00:00:00",
        "end_datetime": "2026-03-01T00:00:00",
        "status": "draft",
        "image_url": None,
        "is_official": 0,
        "created_at": None,
    }
    defaults.update(overrides)
    return defaults


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
