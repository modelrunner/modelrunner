"""Uploads speak the protocol the API actually implements.

The shapes asserted here are taken from apps/api in the mrun repo:

  POST /storage/upload/initiate         -> {upload_url, file_url}   (snake)
  POST /storage/upload/initiate-multipart -> {fileUrl, uploadId, uploadKey} (camel)
  GET  /storage/upload/multipart-url    -> {presignedUrl}
  POST /storage/upload/complete         -> {fileUrl}

The two initiate endpoints genuinely disagree on casing, which is what the
previous multipart implementation tripped over: it read `upload_url` from the
camelCase response and raised KeyError before uploading a byte.

Retention rides on a different call per path -- initiate for single-part,
complete for multipart -- because that is where the API creates the file row.
"""

import json

import httpx
import pytest

from modelrunner_ai import StorageSettings
from modelrunner_ai.client import (
    MULTIPART_THRESHOLD,
    USER_AGENT,
    AsyncClient,
    SyncClient,
)
from modelrunner_ai.storage import OBJECT_LIFECYCLE_PREFERENCE_HEADER

FILE_URL = "https://media.modelrunner.ai/abc123-big.bin"
S3_PART_URL = "https://s3.example.com/bucket/abc123-big.bin?partNumber=1&sig=x"

BIG = b"x" * (MULTIPART_THRESHOLD + 1024)


#: Method and JSON body keys the API's zod schemas accept, per
#: packages/api-contracts/src/storage.ts. Asserted because a client that used
#: the right paths with the wrong method or camelCase body keys would satisfy
#: every response-shape assertion here and still 404 or 400 in production.
API_CONTRACT = {
    "/storage/upload/initiate": ("POST", {"content_type", "file_name"}),
    "/storage/upload/initiate-multipart": ("POST", {"content_type", "file_name"}),
    "/storage/upload/multipart-url": ("GET", None),
    "/storage/upload/complete": ("POST", {"uploadId", "uploadKey", "parts"}),
}


def _api(record):
    """Stand in for apps/api, recording each call."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        record.append((request.method, path, request.headers))

        contract = API_CONTRACT.get(path)
        if contract is not None:
            method, body_keys = contract
            assert (
                request.method == method
            ), f"{path} must be {method}, got {request.method}"
            if body_keys is not None:
                assert (
                    set(json.loads(request.read())) == body_keys
                ), f"{path} body keys do not match the API schema"

        if path.endswith("/upload/initiate"):
            return httpx.Response(
                200, json={"upload_url": S3_PART_URL, "file_url": FILE_URL}
            )
        if path.endswith("/upload/initiate-multipart"):
            return httpx.Response(
                200,
                json={
                    "fileUrl": FILE_URL,
                    "uploadId": "upload-1",
                    "uploadKey": "abc123-big.bin",
                },
            )
        if path.endswith("/upload/multipart-url"):
            record[-1] = (request.method, path, request.url.params)
            return httpx.Response(200, json={"presignedUrl": S3_PART_URL})
        if path.endswith("/upload/complete"):
            record.append(("BODY", json.loads(request.read()), request.headers))
            # Bucket-native location; deliberately NOT what the client returns.
            return httpx.Response(
                200, json={"fileUrl": "https://s3.example.com/bucket/abc123-big.bin"}
            )
        # The presigned S3 PUT for a part.
        return httpx.Response(200, headers={"ETag": '"etag-abc"'})

    return handle


def _route_all(monkeypatch, handler):
    """Point every client the code builds -- including the bare one it opens for
    the presigned PUTs, which is not reachable from the test otherwise -- at the
    same handler."""

    real_client, real_async_client = httpx.Client, httpx.AsyncClient

    def sync_factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return real_client(*args, **kwargs)

    def async_factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", sync_factory)
    monkeypatch.setattr(httpx, "AsyncClient", async_factory)


@pytest.fixture
def record(monkeypatch):
    calls = []
    _route_all(monkeypatch, _api(calls))
    return calls


def _sync() -> SyncClient:
    c = SyncClient(key="test")
    c.__dict__["_client"] = httpx.Client(
        headers={"Authorization": "Key test", "User-Agent": USER_AGENT},
    )
    return c


def _async() -> AsyncClient:
    c = AsyncClient(key="test")
    c.__dict__["_client"] = httpx.AsyncClient(
        headers={"Authorization": "Key test", "User-Agent": USER_AGENT},
    )
    return c


def _paths(record):
    return [p for (m, p, _) in record if isinstance(p, str) and p.startswith("/")]


def _complete_body(record):
    return next(v for (k, v, _) in record if k == "BODY")


def _complete_headers(record):
    return next(h for (k, _, h) in record if k == "BODY")


# --- the multipart flow ----------------------------------------------------


def test_multipart_upload_uses_the_api_flow(record):
    """Regression: this used to read `upload_url` out of the camelCase
    initiate-multipart response and raise KeyError before uploading anything."""

    url = _sync().upload(BIG, "application/octet-stream", file_name="big.bin")

    assert url == FILE_URL
    paths = _paths(record)
    assert "/storage/upload/initiate-multipart" in paths
    assert "/storage/upload/multipart-url" in paths
    assert "/storage/upload/complete" in paths
    # The old protocol -- a presigned /complete -- must be gone.
    assert not any(p.endswith("/1") for p in paths)


async def test_multipart_upload_uses_the_api_flow_async(record):
    url = await _async().upload(BIG, "application/octet-stream", file_name="big.bin")

    assert url == FILE_URL
    assert "/storage/upload/complete" in _paths(record)


def test_multipart_complete_carries_the_parts_in_the_api_shape(record):
    _sync().upload(BIG, "application/octet-stream", file_name="big.bin")

    body = _complete_body(record)
    assert body["uploadId"] == "upload-1"
    assert body["uploadKey"] == "abc123-big.bin"
    # Sequential from 1, each carrying the ETag S3 answered the part PUT with.
    assert [p["partNumber"] for p in body["parts"]] == list(
        range(1, len(body["parts"]) + 1)
    )
    assert {p["etag"] for p in body["parts"]} == {'"etag-abc"'}


def test_multipart_part_urls_are_requested_per_part(record):
    _sync().upload(BIG, "application/octet-stream", file_name="big.bin")

    part_calls = [
        params
        for (m, p, params) in record
        if isinstance(p, str) and p.endswith("/multipart-url")
    ]
    assert part_calls, "no part url was requested"
    for i, params in enumerate(part_calls, start=1):
        assert params["uploadKey"] == "abc123-big.bin"
        assert params["uploadId"] == "upload-1"
        assert params["partNumber"] == str(i)


def test_multipart_returns_the_initiate_url_not_the_s3_location(record):
    """The API keys the file row by the url initiate handed back; complete
    answers with the bucket-native location, a different string for the same
    object."""

    url = _sync().upload(BIG, "application/octet-stream", file_name="big.bin")

    assert url == FILE_URL
    assert "s3.example.com" not in url


# --- where retention rides -------------------------------------------------


def test_multipart_sends_the_lifecycle_on_complete_not_initiate(record):
    """A multipart upload's row is created at complete, so that is the only call
    on which the expiry is recorded."""

    _sync().upload(
        BIG,
        "application/octet-stream",
        file_name="big.bin",
        lifecycle=StorageSettings("1h"),
    )

    assert json.loads(
        _complete_headers(record)[OBJECT_LIFECYCLE_PREFERENCE_HEADER]
    ) == {"expiration_duration_seconds": 3600}

    initiate = next(
        h
        for (m, p, h) in record
        if isinstance(p, str) and p.endswith("/initiate-multipart")
    )
    assert OBJECT_LIFECYCLE_PREFERENCE_HEADER not in initiate


def test_singlepart_sends_the_lifecycle_on_initiate(record):
    _sync().upload(
        b"small", "text/plain", file_name="s.txt", lifecycle=StorageSettings("1h")
    )

    initiate = next(
        h
        for (m, p, h) in record
        if isinstance(p, str) and p.endswith("/upload/initiate")
    )
    assert json.loads(initiate[OBJECT_LIFECYCLE_PREFERENCE_HEADER]) == {
        "expiration_duration_seconds": 3600
    }


async def test_singlepart_sends_the_lifecycle_on_initiate_async(record):
    await _async().upload(
        b"small", "text/plain", file_name="s.txt", lifecycle=StorageSettings("never")
    )

    initiate = next(
        h
        for (m, p, h) in record
        if isinstance(p, str) and p.endswith("/upload/initiate")
    )
    assert initiate[OBJECT_LIFECYCLE_PREFERENCE_HEADER] == (
        '{"expiration_duration_seconds":null}'
    )


def test_no_lifecycle_header_when_none_is_asked_for(record):
    _sync().upload(b"small", "text/plain", file_name="s.txt")

    initiate = next(
        h
        for (m, p, h) in record
        if isinstance(p, str) and p.endswith("/upload/initiate")
    )
    assert OBJECT_LIFECYCLE_PREFERENCE_HEADER not in initiate


@pytest.mark.parametrize("bad", [StorageSettings("immediate"), StorageSettings(1)])
@pytest.mark.parametrize("payload", [b"small", BIG], ids=["singlepart", "multipart"])
def test_an_invalid_lifecycle_fails_before_anything_is_uploaded(bad, payload, record):
    """On the multipart path the header only rides on the complete call, so a
    naive implementation validates it *after* transferring the whole payload."""

    with pytest.raises(ValueError):
        _sync().upload(payload, "text/plain", file_name="s.txt", lifecycle=bad)

    assert record == []


def test_svg_repeats_the_content_disposition_the_presign_was_signed_with(monkeypatch):
    """The API signs an SVG's PUT with Content-Disposition: attachment; a PUT
    that omits it is rejected with SignatureDoesNotMatch."""

    seen = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload/initiate"):
            return httpx.Response(
                200, json={"upload_url": S3_PART_URL, "file_url": FILE_URL}
            )
        seen["headers"] = request.headers
        return httpx.Response(200)

    _route_all(monkeypatch, handle)
    c = SyncClient(key="test")
    c.__dict__["_client"] = httpx.Client()
    c._singlepart_upload_pre_signed(
        b"<svg/>", "image/svg+xml", "logo.svg", lifecycle=None
    )

    assert seen["headers"]["content-disposition"] == "attachment"
    assert seen["headers"]["content-type"] == "image/svg+xml"


def test_a_normal_content_type_sends_no_content_disposition(monkeypatch):
    seen = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload/initiate"):
            return httpx.Response(
                200, json={"upload_url": S3_PART_URL, "file_url": FILE_URL}
            )
        seen["headers"] = request.headers
        return httpx.Response(200)

    _route_all(monkeypatch, handle)
    c = SyncClient(key="test")
    c.__dict__["_client"] = httpx.Client()
    c._singlepart_upload_pre_signed(b"hi", "text/plain", "a.txt", lifecycle=None)

    assert "content-disposition" not in seen["headers"]


def test_upload_file_threads_the_lifecycle_through(tmp_path, record):
    path = tmp_path / "note.txt"
    path.write_bytes(b"hello")

    _sync().upload_file(path, lifecycle=StorageSettings("7d"))

    initiate = next(
        h
        for (m, p, h) in record
        if isinstance(p, str) and p.endswith("/upload/initiate")
    )
    assert json.loads(initiate[OBJECT_LIFECYCLE_PREFERENCE_HEADER]) == {
        "expiration_duration_seconds": 604800
    }
