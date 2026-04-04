from fastapi import HTTPException

class KnownHttpException(HTTPException):
    pass

class NotFound(KnownHttpException):
    def __init__(self, resource: str, identifier: str | int):
        super().__init__(status_code=404, detail=f"{resource} with id '{identifier}' not found or does not exist")

class Conflict(KnownHttpException):
    def __init__(self, resource: str, identifier: str | int):
        super().__init__(status_code=409, detail=f"{resource} with id '{identifier}' already exists")

class FormConflict(Conflict):
    def __init__(self, event_id: int):
        super().__init__("Form for event", event_id)

class ActionNotFound(NotFound):
    def __init__(self, id: str | int):
        super().__init__("Action", id)

class MemberNotFound(NotFound):
    def __init__(self, id: str | int):
        super().__init__("Member", id)