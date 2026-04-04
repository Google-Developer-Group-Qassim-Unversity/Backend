from http import HTTPStatus
from httpx import Response
def assert_2xx(response: Response):
    assert 200 <= response.status_code <= 299, "\n".join([
            "\nAssertion failed:",
            "\tExpected: 2xx",
            f"\tActual:   {response.status_code} {HTTPStatus(response.status_code).name}",
            f"\tResponse body: {response.text}",
        ])

def assert_forbidden(response: Response):
    assert response.status_code == 403, "\n".join([
            "\nAssertion failed:",
            "\tExpected: 403 Forbidden",
            f"\tActual:   {response.status_code} {HTTPStatus(response.status_code).name}",
            f"\tResponse body: {response.text}",
        ])

def assert_not_found(response: Response):
    assert response.status_code == 404, "\n".join([
            "\nAssertion failed:",
            "\tExpected: 404 Not Found",
            f"\tActual:   {response.status_code} {HTTPStatus(response.status_code).name}",
            f"\tResponse body: {response.text}",
        ])

def assert_unprocessable(response: Response):
    assert response.status_code == 422, "\n".join([
            "\nAssertion failed:",
            "\tExpected: 422 Unprocessable Content",
            f"\tActual:   {response.status_code} {HTTPStatus(response.status_code).name}",
            f"\tResponse body: {response.text}",
        ])

def assert_existing(response: Response):
    assert response.status_code == 409, "\n".join([
            "\nAssertion failed:",
            "\tExpected: 409 Conflict",
            f"\tActual:   {response.status_code} {HTTPStatus(response.status_code).name}",
            f"\tResponse body: {response.text}",
        ])