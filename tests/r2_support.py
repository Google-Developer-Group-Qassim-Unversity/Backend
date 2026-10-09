"""Cloudflare R2, faked with moto's in-memory S3, for tests that upload files."""

import boto3
import pytest
from moto import mock_aws

from app.clients import get_r2_client
from app.main import app

R2_ENV = {
    "R2_ACCOUNT_ID": "test_account",
    "R2_ACCESS_KEY_ID": "test_key_id",
    "R2_SECRET_ACCESS_KEY": "test_secret_key",
    "R2_BUCKET_NAME": "test-bucket",
    "R2_PUBLIC_URL": "https://cdn.example.com",
}

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
JPEG = b"\xff\xd8\xff" + b"\x00" * 100


@pytest.fixture
def r2_env(monkeypatch):
    for key, value in R2_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def fake_r2():
    """Point the R2 dependency at moto's in-memory S3.

    This is why `get_r2_client` moved into `app/clients.py` as a dependency: the
    test overrides it the same way it overrides the database session, instead of
    patching the module a route happens to import it from.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=R2_ENV["R2_BUCKET_NAME"])
        app.dependency_overrides[get_r2_client] = lambda: s3
        yield s3
        app.dependency_overrides.pop(get_r2_client, None)
