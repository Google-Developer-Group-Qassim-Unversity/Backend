from http import HTTPStatus
from httpx import Response
def assert_2xx(response: Response):
    assert 200 <= response.status_code <= 299, "\n".join([
            "\nAssertion failed:",
            f"\tExpected: 2xx",
            f"\tActual:   {response.status_code} {HTTPStatus(response.status_code).name}",
            f"\tResponse body: {response.text}",
        ])

def assert_forbidden(response: Response):
    assert response.status_code == 403, "\n".join([
            "\nAssertion failed:",
            f"\tExpected: 403 Forbidden",
            f"\tActual:   {response.status_code} {HTTPStatus(response.status_code).name}",
            f"\tResponse body: {response.text}",
        ])