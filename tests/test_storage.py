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


def test_unknown_client_error_contains_safe_b2_code(monkeypatch):
    from botocore.exceptions import ClientError
    exc = ClientError({'Error': {'Code': 'InvalidBucketName', 'Message': 'bad bucket'}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'PutObject')
    err = _friendly_b2_error(exc, 'enviar o arquivo')
    assert err.code == 'storage_config'


def test_upload_uses_put_object(monkeypatch):
    import io
    import app.storage as storage
    class FakeClient:
        def __init__(self): self.calls = []
        def put_object(self, **kwargs): self.calls.append(kwargs)
    fake = FakeClient()
    monkeypatch.setenv('B2_KEY_ID', 'key')
    monkeypatch.setenv('B2_APPLICATION_KEY', 'secret')
    monkeypatch.setenv('B2_BUCKET_NAME', 'SitPython')
    monkeypatch.setenv('B2_ENDPOINT', 'https://s3.us-east-005.backblazeb2.com')
    monkeypatch.setattr(storage, '_client', lambda: fake)
    class F:
        filename='teste.pdf'; mimetype='application/pdf'; stream=io.BytesIO(b'abc')
    from flask import Flask
    with Flask(__name__).app_context():
        key = storage.upload(F(), 'teste.pdf', 'application/pdf')
    assert key.startswith('materials/')
    assert fake.calls[0]['Bucket'] == 'sitpython'
    assert fake.calls[0]['ContentType'] == 'application/pdf'


def test_b2_bucket_name_is_normalized_for_s3_compatibility(monkeypatch):
    import app.storage as storage
    monkeypatch.setenv('B2_BUCKET_NAME', 'SitPython')
    assert storage._bucket() == 'sitpython'
