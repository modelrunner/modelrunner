"""The data-retention options reach the wire on every entry point.

Both headers are the kind of option whose failure mode is silence -- a dropped
lifecycle looks exactly like a successful one until the media outlives its
expiry -- so each of run/submit/subscribe/stream is checked on both clients
rather than trusting that they share a code path.
"""

import json

import httpx
import pytest

from modelrunner_ai import StorageSettings
from modelrunner_ai.client import USER_AGENT, AsyncClient, SyncClient
from modelrunner_ai.storage import (
    OBJECT_LIFECYCLE_PREFERENCE_HEADER,
    STORE_IO_HEADER,
)

RUNNER_HINT_HEADER = "x-modelrunner-runner-hint"
QUEUE_PRIORITY_HEADER = "x-modelrunner-queue-priority"

ENVELOPE = {
    "status": "IN_PROGRESS",
    "request_id": "req-1",
    "response_url": "https://queue.modelrunner.run/owner/app/requests/req-1",
    "status_url": "https://queue.modelrunner.run/owner/app/requests/req-1/status",
    "cancel_url": "https://queue.modelrunner.run/owner/app/requests/req-1/cancel",
    "queue_position": 0,
    "logs": None,
}

RESULT = {"status": "COMPLETED", "output": "https://media.modelrunner.ai/out.png"}

RETENTION = {"lifecycle": StorageSettings("1h"), "store_io": False}


def _recorder(sse=False):
    """Record the first POST's headers, then answer well enough for the caller
    to run to completion."""

    seen: dict = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "headers" not in seen:
            seen["headers"] = request.headers

        if sse:
            return httpx.Response(
                200,
                content=b'data: {"chunk": 1}\n\n',
                headers={"content-type": "text/event-stream"},
            )
        if request.method == "POST":
            return httpx.Response(200, json=ENVELOPE)
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED", "logs": []})
        return httpx.Response(200, json=RESULT)

    return seen, handle


def _sync_client(handler) -> SyncClient:
    client = SyncClient(key="test")
    # cached_property writes straight to __dict__, which the frozen dataclass
    # does not guard, so this stands in for the real transport while keeping the
    # client-level headers the real one sets.
    client.__dict__["_client"] = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Key test", "User-Agent": USER_AGENT},
    )
    return client


def _async_client(handler) -> AsyncClient:
    client = AsyncClient(key="test")
    client.__dict__["_client"] = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Key test", "User-Agent": USER_AGENT},
    )
    return client


def _assert_retention(headers: httpx.Headers) -> None:
    assert json.loads(headers[OBJECT_LIFECYCLE_PREFERENCE_HEADER]) == {
        "expiration_duration_seconds": 3600
    }
    assert headers[STORE_IO_HEADER] == "0"


# --- every entry point, both clients ---------------------------------------


@pytest.mark.parametrize("method", ["run", "submit", "subscribe"])
def test_sync_queue_methods_send_the_retention_headers(method):
    seen, handle = _recorder()
    getattr(_sync_client(handle), method)("owner/app", arguments={}, **RETENTION)

    _assert_retention(seen["headers"])


@pytest.mark.parametrize("method", ["run", "submit", "subscribe"])
async def test_async_queue_methods_send_the_retention_headers(method):
    seen, handle = _recorder()
    await getattr(_async_client(handle), method)("owner/app", arguments={}, **RETENTION)

    _assert_retention(seen["headers"])


def test_sync_stream_sends_the_retention_headers():
    seen, handle = _recorder(sse=True)
    list(_sync_client(handle).stream("owner/app", arguments={}, **RETENTION))

    _assert_retention(seen["headers"])


async def test_async_stream_sends_the_retention_headers():
    seen, handle = _recorder(sse=True)
    async for _ in _async_client(handle).stream("owner/app", arguments={}, **RETENTION):
        pass

    _assert_retention(seen["headers"])


# --- raw headers and precedence --------------------------------------------


def test_raw_headers_are_sent():
    seen, handle = _recorder()
    _sync_client(handle).run(
        "owner/app", arguments={}, headers={"X-Experimental-Thing": "yes"}
    )

    assert seen["headers"]["x-experimental-thing"] == "yes"


def test_typed_options_win_over_the_same_header_passed_raw():
    """A raw header differing only in case must not survive alongside the typed
    option and be sent twice."""

    seen, handle = _recorder()
    _sync_client(handle).submit(
        "owner/app",
        arguments={},
        hint="a100",
        priority="low",
        lifecycle=StorageSettings("1h"),
        store_io=False,
        headers={
            "X-Modelrunner-Runner-Hint": "ignored",
            "x-modelrunner-queue-priority": "normal",
            "X-Modelrunner-Object-Lifecycle-Preference": "{}",
            "X-Modelrunner-Store-IO": "1",
        },
    )

    headers = seen["headers"]
    assert headers[RUNNER_HINT_HEADER] == "a100"
    assert headers[QUEUE_PRIORITY_HEADER] == "low"
    _assert_retention(headers)

    for name in (
        RUNNER_HINT_HEADER,
        QUEUE_PRIORITY_HEADER,
        OBJECT_LIFECYCLE_PREFERENCE_HEADER,
        STORE_IO_HEADER,
    ):
        assert headers.get_list(name) == [headers[name]], f"{name} sent more than once"


@pytest.mark.parametrize("reserved", ["accept", "cache-control"])
def test_stream_drops_the_headers_the_sse_transport_owns(reserved):
    """httpx_sse writes Accept/Cache-Control capitalized into the dict it is
    handed, and httpx keeps names differing only in case as separate entries --
    so a lowercase copy from the caller would be sent alongside it rather than
    replaced, putting two Accept lines on the wire."""

    seen, handle = _recorder(sse=True)
    list(
        _sync_client(handle).stream(
            "owner/app",
            arguments={},
            headers={reserved: "application/json", "x-kept": "yes"},
            **RETENTION,
        )
    )

    assert seen["headers"].get_list(reserved) == [
        "text/event-stream" if reserved == "accept" else "no-store"
    ]
    # Dropping the reserved names must not drop anything else.
    assert seen["headers"]["x-kept"] == "yes"
    _assert_retention(seen["headers"])


def test_per_request_headers_do_not_clobber_the_client_defaults():
    seen, handle = _recorder()
    _sync_client(handle).run("owner/app", arguments={}, **RETENTION)

    assert seen["headers"]["authorization"] == "Key test"
    assert seen["headers"]["user-agent"] == USER_AGENT


def test_no_retention_headers_are_sent_when_no_option_is_given():
    """The account-wide default has to stay reachable -- an unset option must
    not resolve to a client-side default."""

    seen, handle = _recorder()
    _sync_client(handle).run("owner/app", arguments={})

    assert OBJECT_LIFECYCLE_PREFERENCE_HEADER not in seen["headers"]
    assert STORE_IO_HEADER not in seen["headers"]


# --- validation happens before the request goes out ------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lifecycle": StorageSettings("immediate")},
        {"lifecycle": StorageSettings(0)},
        {"lifecycle": "1h"},
        {"store_io": "0"},
        {"headers": {"x-thing": 1}},
        {"headers": "x-thing: yes"},
    ],
)
def test_invalid_options_raise_before_anything_is_sent(kwargs):
    seen, handle = _recorder()

    with pytest.raises(ValueError):
        _sync_client(handle).run("owner/app", arguments={}, **kwargs)

    assert "headers" not in seen
