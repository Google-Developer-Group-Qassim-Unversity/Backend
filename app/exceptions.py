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


class EmptyBody(KnownHttpException):
    def __init__(self):
        super().__init__(status_code=400, detail="Request body must contain HTML content")


class GatewayTimeout(KnownHttpException):
    def __init__(self, detail: str = "Upstream request timed out"):
        super().__init__(status_code=504, detail=detail)


class BadGateway(KnownHttpException):
    def __init__(self, detail: str = "Upstream returned an error"):
        super().__init__(status_code=502, detail=detail)


class ServiceUnavailable(KnownHttpException):
    def __init__(self, detail: str = "Failed to connect to upstream service"):
        super().__init__(status_code=503, detail=detail)


class GoogleFormAuthExpired(KnownHttpException):
    """Google rejected the refresh token stored on the form.

    Distinct from `NotFound` (no such form) and from a plain `BadGateway`: the
    form exists and Google is up, but the link an admin made when they attached
    the form has been revoked or expired. Re-linking is the fix, so the message
    says so rather than sending anyone to the logs.
    """

    def __init__(self, google_form_id: str):
        super().__init__(
            status_code=502,
            detail=(
                f"Google rejected the stored credentials for form '{google_form_id}'. "
                "Re-link the form from the event's manage page."
            ),
        )


class GoogleFormNotLinked(KnownHttpException):
    """The form row has no refresh token, so its responses cannot be read at all."""

    def __init__(self, google_form_id: str):
        super().__init__(
            status_code=409,
            detail=(
                f"Form '{google_form_id}' is not linked to a Google account. "
                "Link it from the event's manage page before syncing."
            ),
        )


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


class EmailTemplateNotFound(NotFound):
    def __init__(self, id: str | int):
        super().__init__("Email template", id)


class SemesterNotFound(NotFound):
    def __init__(self, id: str | int):
        super().__init__("Semester", id)


class NoSemestersDefined(KnownHttpException):
    def __init__(self):
        super().__init__(
            status_code=409,
            detail="No semesters are defined. Add one from the admin app before using semester-scoped endpoints.",
        )
