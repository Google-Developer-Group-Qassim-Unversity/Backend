from fastapi import HTTPException


class KnownHttpException(HTTPException):
    """A deliberate, expected failure that is part of the API contract.

    `code` is the optional machine-readable half of the response. `detail` is
    written for whoever is reading logs; clients that show a message to a user
    should switch on `code` and supply their own localized copy, because only
    the client knows the reader's language.
    """

    code: str | None = None


class NotFound(KnownHttpException):
    def __init__(self, resource: str, identifier: str | int):
        super().__init__(status_code=404, detail=f"{resource} with id '{identifier}' not found or does not exist")


class Conflict(KnownHttpException):
    def __init__(self, resource: str, identifier: str | int):
        super().__init__(status_code=409, detail=f"{resource} with id '{identifier}' already exists")


class ClubStructureConflict(KnownHttpException):
    def __init__(self, detail: str):
        super().__init__(status_code=409, detail=detail)


class InvalidClubStructure(KnownHttpException):
    def __init__(self, detail: str):
        super().__init__(status_code=422, detail=detail)


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
    """Google rejected the club account's stored credentials.

    Distinct from `NotFound` (no such form) and from a plain `BadGateway`: the
    form exists and Google is up, but the one refresh token every form is read
    with (see docs/GOOGLE_FORMS.md) has been revoked or expired - not
    something re-attaching this one form can fix, since it's a single
    app-wide credential, not a per-form link.
    """

    def __init__(self, google_form_id: str):
        super().__init__(
            status_code=502,
            detail=(
                f"Google rejected the club account's stored credentials while reading form '{google_form_id}'. "
                "Run scripts/setup_google_oauth.py again and update GOOGLE_REFRESH_TOKEN in Infisical."
            ),
        )


class FormNotFoundById(NotFound):
    def __init__(self, form_id: int):
        super().__init__("Form", form_id)


class FormNotFound(NotFound):
    def __init__(self, event_id: int):
        super().__init__("Form for event", event_id)


class FormNotAttached(KnownHttpException):
    def __init__(self, form_id: int):
        super().__init__(status_code=409, detail=f"Form {form_id} is not attached to a Google Form yet")


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


class AttendanceTokenError(KnownHttpException):
    """The attendance token in a check-in request could not be accepted.

    Every subclass carries a `code` because the member app renders a message
    for a student standing at the door, in their own language. `detail` stays
    English and developer-facing; it is never what the student reads.
    """

    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


class AttendanceTokenAbsent(AttendanceTokenError):
    def __init__(self):
        super().__init__(400, "token_absent", "No attendance token provided")


class AttendanceTokenMalformed(AttendanceTokenError):
    """Not parseable as a JWT at all - almost always a truncated link.

    A shared attendance URL that loses even its last character arrives here,
    so treat this as "the link is broken", not "the token is wrong".
    """

    def __init__(self):
        super().__init__(400, "token_malformed", "Malformed attendance token")


class AttendanceTokenExpired(AttendanceTokenError):
    def __init__(self):
        super().__init__(401, "token_expired", "Attendance token has expired")


class AttendanceTokenNotYetValid(AttendanceTokenError):
    def __init__(self):
        super().__init__(401, "token_not_yet_valid", "Attendance token is not valid yet")


class AttendanceTokenBadSignature(AttendanceTokenError):
    def __init__(self):
        super().__init__(401, "token_bad_signature", "Invalid attendance token signature")


class AttendanceTokenBadAlgorithm(AttendanceTokenError):
    def __init__(self):
        super().__init__(400, "token_bad_algorithm", "Invalid attendance token algorithm")


class AttendanceTokenMissingClaim(AttendanceTokenError):
    def __init__(self, claim: str):
        super().__init__(400, "token_missing_claim", f"Token missing required claim: {claim}")


class AttendanceTokenEventMismatch(AttendanceTokenError):
    def __init__(self):
        super().__init__(400, "token_event_mismatch", "Token event ID does not match the requested event")


class AttendanceTokenInvalid(AttendanceTokenError):
    def __init__(self, reason: str):
        super().__init__(400, "token_invalid", f"Invalid attendance token ({reason})")
