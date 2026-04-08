from fastapi import HTTPException


class KnownHttpException(HTTPException):
    pass


class NotFound(KnownHttpException):
    def __init__(self, resource: str, identifier: str | int):
        super().__init__(status_code=404, detail=f"{resource} with id '{identifier}' not found or does not exist")


class Conflict(KnownHttpException):
    def __init__(self, resource: str, identifier: str | int):
        super().__init__(status_code=409, detail=f"{resource} with id '{identifier}' already exists")


class DataIntegrityError(HTTPException):
    """Exception raised when a data integrity violation is detected.
    (basically raise whenver somethign that should NEVER happen, happens.)
    """

    def __init__(self, message: str):
        super().__init__(status_code=500, detail=message)


class FormNotFoundById(NotFound):
    def __init__(self, form_id: int):
        super().__init__("Form", form_id)


class FormNotFound(NotFound):
    def __init__(self, event_id: int):
        super().__init__("Form for event", event_id)


class EventNotFound(NotFound):
    def __init__(self, id: str | int):
        super().__init__("Event", id)


class ActionNotFound(NotFound):
    def __init__(self, id: str | int):
        super().__init__("Action", id)


class MemberNotFound(NotFound):
    def __init__(self, id: str | int):
        super().__init__("Member", id)
