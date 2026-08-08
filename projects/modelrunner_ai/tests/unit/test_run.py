import json

import httpx
import pytest

from modelrunner_ai.client import RUN_URL_FORMAT, AsyncClient, SyncClient

ENVELOPE = {
    "status": "IN_PROGRESS",
    "request_id": "req-1",
    "response_url": "https://queue.modelrunner.run/owner/app/requests/req-1",
    "status_url": "https://queue.modelrunner.run/owner/app/requests/req-1/status",
    "cancel_url": "https://queue.modelrunner.run/owner/app/requests/req-1/cancel",
    "queue_position": 3,
    "logs": None,
}

RESULT = {
    "id": "req-1",
    "status": "COMPLETED",
    "output": "https://media.modelrunner.ai/out.png",
    "input": {"prompt": "a brass sextant on a nautical chart"},
}


def _queue_handler(calls, *, in_queue_polls=1, in_progress_polls=1):
    """Answer a run POST with the queue envelope, then walk the status endpoint
    through the queue to COMPLETED before serving the result."""

    def handle(request: httpx.Request) -> httpx.Response:
        # The path, not the url: status is polled with a ?logs= query.
        calls.append(request.url.path)

        if request.method == "POST":
            return httpx.Response(200, json=ENVELOPE)

        if request.url.path.endswith("/status"):
            polls = _status_polls(calls)
            # A client that never leaves the queue would poll forever, so fail
            # the test instead of hanging it.
            assert polls <= in_queue_polls + in_progress_polls + 5
            if polls <= in_queue_polls:
                return httpx.Response(
                    200, json={"status": "IN_QUEUE", "queue_position": 2}
                )
            if polls <= in_queue_polls + in_progress_polls:
                return httpx.Response(200, json={"status": "IN_PROGRESS", "logs": []})
            return httpx.Response(200, json={"status": "COMPLETED", "logs": []})

        return httpx.Response(200, json=RESULT)

    return handle


def _status_polls(calls) -> int:
    return len([c for c in calls if c.endswith("/status")])


def _sync_client(handler) -> SyncClient:
    client = SyncClient(key="test")
    # cached_property writes straight to __dict__, which the frozen dataclass
    # does not guard, so this stands in for the real transport.
    client.__dict__["_client"] = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _async_client(handler) -> AsyncClient:
    client = AsyncClient(key="test")
    client.__dict__["_client"] = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    return client


def test_run_waits_for_the_result():
    calls: list[str] = []
    result = _sync_client(_queue_handler(calls)).run("owner/app", arguments={})

    assert result == RESULT
    assert result["output"] == "https://media.modelrunner.ai/out.png"
    assert _status_polls(calls) == 3


async def test_run_async_waits_for_the_result():
    calls: list[str] = []
    result = await _async_client(_queue_handler(calls)).run("owner/app", arguments={})

    assert result == RESULT
    assert result["output"] == "https://media.modelrunner.ai/out.png"
    assert _status_polls(calls) == 3


@pytest.mark.parametrize(
    "payload",
    [
        RESULT,
        # A partial envelope is not one -- without the urls there is nothing to
        # poll, so the body is all the caller can be given.
        {"request_id": "req-1", "output": "direct"},
        ["not", "a", "dict"],
    ],
)
def test_run_returns_a_non_envelope_body_as_is(payload):
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, json=payload)

    assert _sync_client(handle).run("owner/app", arguments={}) == payload


@pytest.mark.parametrize(
    "payload",
    [
        RESULT,
        {"request_id": "req-1", "output": "direct"},
        ["not", "a", "dict"],
    ],
)
async def test_run_async_returns_a_non_envelope_body_as_is(payload):
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, json=payload)

    assert await _async_client(handle).run("owner/app", arguments={}) == payload


def test_run_still_posts_to_the_run_endpoint_with_its_headers():
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["url"] = str(request.url)
            seen["hint"] = request.headers.get("X-Modelrunner-Runner-Hint")
            seen["body"] = json.loads(request.read())
            return httpx.Response(200, json=ENVELOPE)
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED", "logs": []})
        return httpx.Response(200, json=RESULT)

    _sync_client(handle).run(
        "owner/app",
        arguments={"prompt": "hi"},
        path="/sub",
        hint="a100",
        metadata={"env": "prod"},
    )

    assert seen["url"] == RUN_URL_FORMAT + "owner/app/sub"
    assert seen["hint"] == "a100"
    assert seen["body"] == {"prompt": "hi", "metadata": {"env": "prod"}}
