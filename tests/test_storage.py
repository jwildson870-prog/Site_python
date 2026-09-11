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


def test_native_upload_uses_b2_api(monkeypatch):
    import io
    import app.storage as storage
    from flask import Flask

    class Resp:
        def __init__(self, status=200, data=None, text=''):
            self.status_code = status
            self._data = data or {}
            self.text = text
            self.ok = status < 400
        def json(self):
            return self._data

    calls = []
    responses = [
        Resp(data={'accountId': 'acc', 'authorizationToken': 'auth', 'apiInfo': {'storageApi': {'apiUrl': 'https://api001.backblazeb2.com', 'allowed': {'buckets': [{'id': 'bucket-id', 'name': 'SitPython'}]}}}}),
        Resp(data={'bucketId': 'bucket-id', 'uploadUrl': 'https://pod.backblaze.com/upload', 'authorizationToken': 'upload-token'}),
        Resp(data={'fileId': 'file-id'}),
    ]
    def fake_get(url, **kwargs):
        calls.append(('GET', url, kwargs)); return responses.pop(0)
    def fake_post(url, **kwargs):
        calls.append(('POST', url, kwargs)); return responses.pop(0)

    monkeypatch.setattr(storage.requests, 'get', fake_get)
    monkeypatch.setattr(storage.requests, 'post', fake_post)
    monkeypatch.setenv('B2_KEY_ID', 'key')
    monkeypatch.setenv('B2_APPLICATION_KEY', 'secret')
    monkeypatch.setenv('B2_BUCKET_NAME', 'SitPython')

    class F:
        filename='teste.pdf'; mimetype='application/pdf'; stream=io.BytesIO(b'abc')
    with Flask(__name__).app_context():
        key = storage.upload(F(), 'teste.pdf', 'application/pdf')
    assert key.startswith('materials/')
    assert calls[0][0] == 'GET' and 'b2_authorize_account' in calls[0][1]
    assert calls[1][0] == 'GET' and 'b2_get_upload_url' in calls[1][1]
    assert calls[2][0] == 'POST' and calls[2][1].endswith('/upload')
    assert calls[2][2]['headers']['Content-Length'] == '3'


def test_native_upload_bucket_keeps_real_name(monkeypatch):
    import app.storage as storage
    monkeypatch.setenv('B2_BUCKET_NAME', 'SitPython')
    assert storage._bucket() == 'SitPython'


def test_bucket_legacy_sitpython_aliases_to_real_sitepython(monkeypatch):
    monkeypatch.setenv("B2_BUCKET_NAME", "SitPython")
    assert storage._bucket() == "SitePython"


def test_bucket_default_is_sitepython(monkeypatch):
    monkeypatch.delenv("B2_BUCKET_NAME", raising=False)
    assert storage._bucket() == "SitePython"
