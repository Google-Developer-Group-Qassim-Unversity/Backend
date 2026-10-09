from io import BytesIO

from fastapi.testclient import TestClient

from tests.r2_support import JPEG, PNG, R2_ENV
from tests.utils import assert_2xx, assert_forbidden


def upload(client: TestClient, filename: str, content: bytes, content_type: str, path: str = "/upload/"):
    return client.post(path, files={"file": (filename, BytesIO(content), content_type)})


def test_upload_image(admin_client: TestClient, r2_env, fake_r2):
    response = upload(admin_client, "test.png", PNG, "image/png")

    assert_2xx(response)
    body = response.json()
    assert body["url"].startswith("https://cdn.example.com/event-images/")
    assert body["url"].endswith(".png")


def test_upload_image_no_extension(admin_client: TestClient, r2_env, fake_r2):
    response = upload(admin_client, "filename_without_extension", PNG, "image/png")

    assert_2xx(response)
    body = response.json()
    assert body["url"].startswith("https://cdn.example.com/event-images/")
    assert ".png" in body["url"]


def test_upload_jpeg(admin_client: TestClient, r2_env, fake_r2):
    response = upload(admin_client, "photo.jpeg", JPEG, "image/jpeg")

    assert_2xx(response)
    assert response.json()["url"].endswith(".jpeg")


def test_uploaded_object_actually_lands_in_the_bucket(admin_client: TestClient, r2_env, fake_r2):
    """The old tests asserted on the returned URL only, so a route that built a
    plausible URL without storing anything would have passed."""
    response = upload(admin_client, "stored.png", PNG, "image/png")

    assert_2xx(response)
    key = response.json()["url"].removeprefix("https://cdn.example.com/")
    stored = fake_r2.get_object(Bucket=R2_ENV["R2_BUCKET_NAME"], Key=key)
    assert stored["Body"].read() == PNG
    assert stored["ContentType"] == "image/png"


def test_email_attachment_returns_its_metadata(admin_client: TestClient, r2_env, fake_r2):
    response = upload(admin_client, "invoice.pdf", b"%PDF-1.4 fake", "application/pdf", "/upload/email-attachment")

    assert_2xx(response)
    body = response.json()
    assert body["url"].startswith("https://cdn.example.com/email-attachments/")
    assert body["filename"] == "invoice.pdf"
    assert body["content_type"] == "application/pdf"
    assert body["size"] == len(b"%PDF-1.4 fake")


def test_email_attachment_rejects_unsupported_type(admin_client: TestClient, r2_env, fake_r2):
    response = upload(admin_client, "script.sh", b"#!/bin/sh", "application/x-sh", "/upload/email-attachment")

    assert response.status_code == 400
    assert "Unsupported attachment type" in response.json()["detail"]


def test_upload_unauthenticated(client: TestClient, r2_env):
    assert_forbidden(upload(client, "test.png", PNG, "image/png"))


def test_upload_regular_member(clerk_client: TestClient, r2_env):
    assert_forbidden(upload(clerk_client, "test.png", PNG, "image/png"))
