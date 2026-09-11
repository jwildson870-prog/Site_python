import os

import pytest

from app.storage import _client, _friendly_b2_error, StorageError


def test_no_such_bucket_is_not_reported_as_missing_file():
    class E(Exception):
        response = {"Error": {"Code": "NoSuchBucket", "Message": "bucket missing"}, "ResponseMetadata": {"HTTPStatusCode": 404}}

    err = _friendly_b2_error(E(), "enviar o arquivo")
    assert isinstance(err, StorageError)
    assert err.code == "storage_bucket_not_found"
    assert "bucket" in err.message.lower()


def test_no_such_key_is_reported_as_missing_file():
    class E(Exception):
        response = {"Error": {"Code": "NoSuchKey", "Message": "key missing"}, "ResponseMetadata": {"HTTPStatusCode": 404}}

    err = _friendly_b2_error(E(), "abrir o arquivo")
    assert err.code == "storage_not_found"


def test_b2_client_uses_path_style_and_s3v4(monkeypatch):
    monkeypatch.setenv("B2_KEY_ID", "key")
    monkeypatch.setenv("B2_APPLICATION_KEY", "secret")
    monkeypatch.setenv("B2_BUCKET_NAME", "SitPython")
    monkeypatch.setenv("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com/")
    monkeypatch.setenv("B2_REGION", "us-east-005")
    client = _client()
    assert client.meta.endpoint_url == "https://s3.us-east-005.backblazeb2.com"
    assert client.meta.config.signature_version == "s3v4"
    assert client.meta.config.s3["addressing_style"] == "path"
