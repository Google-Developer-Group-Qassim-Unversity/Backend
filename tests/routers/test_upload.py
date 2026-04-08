import os
from io import BytesIO
from unittest.mock import patch

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from tests.utils import assert_2xx, assert_forbidden

R2_ENV = {
    "R2_ACCOUNT_ID": "test_account",
    "R2_ACCESS_KEY_ID": "test_key_id",
    "R2_SECRET_ACCESS_KEY": "test_secret_key",
    "R2_BUCKET_NAME": "test-bucket",
    "R2_PUBLIC_URL": "https://cdn.example.com",
}


@pytest.fixture
def r2_env(monkeypatch):
    for key, value in R2_ENV.items():
        monkeypatch.setenv(key, value)


def make_mock_s3_client():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=R2_ENV["R2_BUCKET_NAME"])
    return s3


@mock_aws
def test_upload_image(admin_client: TestClient, r2_env):
    s3 = make_mock_s3_client()

    with patch("app.routers.upload.get_r2_client", return_value=s3):
        image_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        files = {"file": ("test.png", BytesIO(image_content), "image/png")}

        response = admin_client.post("/upload/", files=files)

        assert_2xx(response)
        body = response.json()
        assert "url" in body
        assert body["url"].startswith("https://cdn.example.com/event-images/")
        assert body["url"].endswith(".png")


@mock_aws
def test_upload_image_no_extension(admin_client: TestClient, r2_env):
    s3 = make_mock_s3_client()

    with patch("app.routers.upload.get_r2_client", return_value=s3):
        image_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        files = {"file": ("filename_without_extension", BytesIO(image_content), "image/png")}

        response = admin_client.post("/upload/", files=files)

        assert_2xx(response)
        body = response.json()
        assert "url" in body
        assert body["url"].startswith("https://cdn.example.com/event-images/")
        assert ".png" in body["url"]


@mock_aws
def test_upload_jpeg(admin_client: TestClient, r2_env):
    s3 = make_mock_s3_client()

    with patch("app.routers.upload.get_r2_client", return_value=s3):
        image_content = b"\xff\xd8\xff" + b"\x00" * 100
        files = {"file": ("photo.jpeg", BytesIO(image_content), "image/jpeg")}

        response = admin_client.post("/upload/", files=files)

        assert_2xx(response)
        body = response.json()
        assert body["url"].endswith(".jpeg")


def test_upload_unauthenticated(client: TestClient, r2_env):
    image_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    files = {"file": ("test.png", BytesIO(image_content), "image/png")}

    response = client.post("/upload/", files=files)

    assert_forbidden(response)


def test_upload_regular_member(clerk_client: TestClient, r2_env):
    image_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    files = {"file": ("test.png", BytesIO(image_content), "image/png")}

    response = clerk_client.post("/upload/", files=files)

    assert_forbidden(response)
