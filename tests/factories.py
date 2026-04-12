def make_member(**overrides):
    defaults = {
        "name": "Test Member",
        "email": "test@example.com",
        "phone_number": "0501112233",
        "uni_id": "999999999",
        "gender": "Male",
        "uni_level": 4,
        "uni_college": "Engineering",
    }
    defaults.update(overrides)
    return defaults


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


def make_create_event_payload(seed_refs=None, **overrides):
    event = overrides.pop("event", None) or make_event()
    if seed_refs is not None:
        dept_action_id = seed_refs.dept_action.id
        member_action_id = seed_refs.member_action.id
        department_id = seed_refs.dept_business.id
    else:
        dept_action_id = 1
        member_action_id = 2
        department_id = 1
    defaults = {
        "event": event,
        "form_type": "none",
        "department_action_id": dept_action_id,
        "member_action_id": member_action_id,
        "department_id": department_id,
    }
    defaults.update(overrides)
    return defaults
