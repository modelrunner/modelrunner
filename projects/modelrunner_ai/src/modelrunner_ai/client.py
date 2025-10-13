from __future__ import annotations

import io
import math
import os
import mimetypes
import asyncio
import random
import time
import base64
import logging
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, AsyncIterator, Dict, Iterator, TYPE_CHECKING, Optional, Literal
from urllib.parse import urlencode, urlparse

import httpx
from httpx_sse import aconnect_sse, connect_sse
from modelrunner_ai.auth import MODELRUNNER_RUN_HOST, fetch_credentials

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from PIL import Image

AnyJSON = Dict[str, Any]
Priority = Literal["normal", "low"]

RUN_URL_FORMAT = f"https://{MODELRUNNER_RUN_HOST}/"
QUEUE_URL_FORMAT = f"https://queue.{MODELRUNNER_RUN_HOST}/"
REST_URL = "https://modelrunner.run"
USER_AGENT = "modelrunner-ai/0.2.2 (python)"


MULTIPART_THRESHOLD = 90 * 1024 * 1024
MULTIPART_CHUNK_SIZE = 10 * 1024 * 1024
MULTIPART_MAX_CONCURRENCY = 10


def _extension_from_content_type(content_type: str) -> str:
    try:
        _, file_type = content_type.split("/", 1)
        return file_type.split("-")[0].split(";")[0] or "bin"
    except Exception:
        return "bin"


def _default_filename(content_type: str) -> str:
    return f"{int(time.time() * 1000)}.{_extension_from_content_type(content_type)}"


class ModelrunnerClientError(Exception):
    pass


@dataclass
class ModelrunnerClientHTTPError(ModelrunnerClientError):
    message: str
    status_code: int
    response_headers: dict[str, str]

    def __str__(self) -> str:
        return f"{self.message}"


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            msg = response.json()["detail"]
        except (ValueError, KeyError):
            msg = response.text

        raise ModelrunnerClientHTTPError(
            msg,
            response.status_code,
            # converting to dict to avoid httpx.Headers,
            # which means we don't support multiple values per header
            dict(response.headers),
        ) from exc


@dataclass
class Status: ...


@dataclass
class Queued(Status):
    """Indicates the request is enqueued and waiting to be processed. The position
    field indicates the relative position in the queue (0-indexed)."""

    position: int


@dataclass
class InProgress(Status):
    """Indicates the request is currently being processed. If the status operation called
    with the `with_logs` parameter set to True, the logs field will be a list of
    log objects."""

    # TODO: Type the log object structure so we can offer editor completion
    logs: list[dict[str, Any]] | None = field()


@dataclass
class Completed(Status):
    """Indicates the request has been completed and the result can be gathered. The logs field will
    contain the logs if the status operation was called with the `with_logs` parameter set to True. Metrics
    might contain the inference time, and other internal metadata (number of tokens
    processed, etc.)."""

    logs: list[dict[str, Any]] | None = field()
    metrics: dict[str, Any] = field()


@dataclass(frozen=True)
class _BaseRequestHandle:
    request_id: str
    response_url: str = field(repr=False)
    status_url: str = field(repr=False)
    cancel_url: str = field(repr=False)

    def _parse_status(self, data: AnyJSON) -> Status:
        if data["status"] == "IN_QUEUE":
            return Queued(position=data["queue_position"])
        elif data["status"] == "IN_PROGRESS":
            return InProgress(logs=data["logs"])
        elif data["status"] == "COMPLETED":
            # NOTE: legacy apps might not return metrics
            metrics = data.get("metrics", {})
            return Completed(logs=data["logs"], metrics=metrics)
        else:
            raise ValueError(f"Unknown status: {data['status']}")


APP_NAMESPACES = ["workflows", "comfy"]


def _ensure_app_id_format(id: str) -> str:
    import re

    parts = id.split("/")
    if len(parts) > 1:
        return id

    match = re.match(r"^([0-9]+)-([a-zA-Z0-9-]+)$", id)
    if match:
        app_owner, app_id = match.groups()
        return f"{app_owner}/{app_id}"

    raise ValueError(f"Invalid app id: {id}. Must be in the format <appOwner>/<appId>")


@dataclass(frozen=True)
class AppId:
    owner: str
    alias: str
    path: Optional[str]
    namespace: Optional[str]

    @classmethod
    def from_endpoint_id(cls, endpoint_id: str) -> AppId:
        normalized_id = _ensure_app_id_format(endpoint_id)
        parts = normalized_id.split("/")

        if parts[0] in APP_NAMESPACES:
            return cls(
                owner=parts[1],
                alias=parts[2],
                path="/".join(parts[3:]) or None,
                namespace=parts[0],
            )

        return cls(
            owner=parts[0],
            alias=parts[1],
            path="/".join(parts[2:]) or None,
            namespace=None,
        )


def _request(
    client: httpx.Client, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    response = client.request(method, url, **kwargs)
    _raise_for_status(response)
    return response


async def _async_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    response = await client.request(method, url, **kwargs)
    _raise_for_status(response)
    return response


MAX_ATTEMPTS = 10
BASE_DELAY = 0.1
MAX_DELAY = 30
RETRY_CODES = [408, 409, 429]


def _should_retry(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True

    if (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in RETRY_CODES
    ):
        return True

    return False


def _get_retry_delay(
    num_retry: int,
    base_delay: float,
    max_delay: float,
    backoff_type: Literal["exponential", "fixed"] = "exponential",
    jitter: bool = False,
) -> float:
    if backoff_type == "exponential":
        delay = min(base_delay * (2 ** (num_retry - 1)), max_delay)
    else:
        delay = min(base_delay, max_delay)

    if jitter:
        delay *= random.uniform(0.5, 1.5)

    return min(delay, max_delay)


def _maybe_retry_request(
    client: httpx.Client, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _request(client, method, url, **kwargs)
        except httpx.HTTPError as exc:
            if _should_retry(exc) and attempt < MAX_ATTEMPTS:
                delay = _get_retry_delay(
                    attempt, BASE_DELAY, MAX_DELAY, "exponential", True
                )
                logger.debug(
                    f"Retrying request to {url} due to {exc} ({MAX_ATTEMPTS - attempt} attempts left)"
                )
                time.sleep(delay)
                continue
            raise


async def _async_maybe_retry_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await _async_request(client, method, url, **kwargs)
        except httpx.HTTPError as exc:
            if _should_retry(exc) and attempt < MAX_ATTEMPTS:
                delay = _get_retry_delay(attempt, 0.1, 10, "exponential", True)
                logger.debug(
                    f"Retrying request to {url} due to {exc} ({MAX_ATTEMPTS - attempt} attempts left)"
                )
                await asyncio.sleep(delay)
                continue
            raise


@dataclass(frozen=True)
class SyncRequestHandle(_BaseRequestHandle):
    client: httpx.Client = field(repr=False)

    @classmethod
    def from_request_id(
        cls, client: httpx.Client, application: str, request_id: str
    ) -> SyncRequestHandle:
        app_id = AppId.from_endpoint_id(application)
        prefix = f"{app_id.namespace}/" if app_id.namespace else ""
        base_url = f"{QUEUE_URL_FORMAT}{prefix}{app_id.owner}/{app_id.alias}/requests/{request_id}"
        return cls(
            request_id=request_id,
            response_url=base_url,
            status_url=base_url + "/status",
            cancel_url=base_url + "/cancel",
            client=client,
        )

    def status(self, *, with_logs: bool = False) -> Status:
        """Returns the status of the request (which can be one of the following:
        Queued, InProgress, Completed). If `with_logs` is True, logs will be included
        for InProgress and Completed statuses."""

        response = _maybe_retry_request(
            self.client,
            "GET",
            self.status_url,
            params={
                "logs": with_logs,
            },
        )
        _raise_for_status(response)

        return self._parse_status(response.json())

    def iter_events(
        self, *, with_logs: bool = False, interval: float = 0.1
    ) -> Iterator[Status]:
        """Continuously poll for the status of the request and yield it at each interval till
        the request is completed. If `with_logs` is True, logs will be included in the response.
        """

        while True:
            status = self.status(with_logs=with_logs)
            yield status
            if isinstance(status, Completed):
                break

            time.sleep(interval)

    def get(self) -> AnyJSON:
        """Wait till the request is completed and return the result of the inference call."""
        for _ in self.iter_events(with_logs=False):
            continue

        response = _maybe_retry_request(self.client, "GET", self.response_url)
        _raise_for_status(response)
        return response.json()

    def cancel(self) -> None:
        """Cancel the request."""
        response = _maybe_retry_request(self.client, "PUT", self.cancel_url)
        _raise_for_status(response)


@dataclass(frozen=True)
class AsyncRequestHandle(_BaseRequestHandle):
    client: httpx.AsyncClient = field(repr=False)

    @classmethod
    def from_request_id(
        cls, client: httpx.AsyncClient, application: str, request_id: str
    ) -> AsyncRequestHandle:
        app_id = AppId.from_endpoint_id(application)
        prefix = f"{app_id.namespace}/" if app_id.namespace else ""
        base_url = f"{QUEUE_URL_FORMAT}{prefix}{app_id.owner}/{app_id.alias}/requests/{request_id}"
        return cls(
            request_id=request_id,
            response_url=base_url,
            status_url=base_url + "/status",
            cancel_url=base_url + "/cancel",
            client=client,
        )

    async def status(self, *, with_logs: bool = False) -> Status:
        """Returns the status of the request (which can be one of the following:
        Queued, InProgress, Completed). If `with_logs` is True, logs will be included
        for InProgress and Completed statuses."""

        response = await _async_maybe_retry_request(
            self.client,
            "GET",
            self.status_url,
            params={
                "logs": with_logs,
            },
        )
        _raise_for_status(response)

        return self._parse_status(response.json())

    async def iter_events(
        self, *, with_logs: bool = False, interval: float = 0.1
    ) -> AsyncIterator[Status]:
        """Continuously poll for the status of the request and yield it at each interval till
        the request is completed. If `with_logs` is True, logs will be included in the response.
        """

        while True:
            status = await self.status(with_logs=with_logs)
            yield status
            if isinstance(status, Completed):
                break

            await asyncio.sleep(interval)

    async def get(self) -> AnyJSON:
        """Wait till the request is completed and return the result."""
        async for _ in self.iter_events(with_logs=False):
            continue

        response = await _async_maybe_retry_request(
            self.client, "GET", self.response_url
        )
        _raise_for_status(response)
        return response.json()

    async def cancel(self) -> None:
        """Cancel the request."""
        response = await _async_maybe_retry_request(self.client, "PUT", self.cancel_url)
        _raise_for_status(response)


@dataclass(frozen=True)
class AsyncClient:
    key: str | None = field(default=None, repr=False)
    default_timeout: float = 120.0

    def _get_key(self) -> str:
        if self.key is None:
            return fetch_credentials()
        return self.key

    @cached_property
    def _client(self) -> httpx.AsyncClient:
        key = self._get_key()
        return httpx.AsyncClient(
            headers={
                "Authorization": f"Key {key}",
                "User-Agent": USER_AGENT,
            },
            timeout=self.default_timeout,
        )

    async def run(
        self,
        application: str,
        arguments: AnyJSON,
        *,
        path: str = "",
        timeout: float | None = None,
        hint: str | None = None,
    ) -> AnyJSON:
        """Run an application with the given arguments (which will be JSON serialized). The path parameter can be used to
        specify a subpath when applicable. This method will return the result of the inference call directly.
        """

        url = RUN_URL_FORMAT + application
        if path:
            url += "/" + path.lstrip("/")

        headers = {}
        if hint is not None:
            headers["X-Modelrunner-Runner-Hint"] = hint

        arguments = await self.transform_arguments(arguments)

        response = await _async_maybe_retry_request(
            self._client,
            "POST",
            url,
            json=arguments,
            timeout=timeout,
            headers=headers,
        )
        _raise_for_status(response)
        return response.json()

    async def submit(
        self,
        application: str,
        arguments: AnyJSON,
        *,
        path: str = "",
        hint: str | None = None,
        webhook_url: str | None = None,
        priority: Optional[Priority] = None,
    ) -> AsyncRequestHandle:
        """Submit an application with the given arguments (which will be JSON serialized). The path parameter can be used to
        specify a subpath when applicable. This method will return a handle to the request that can be used to check the status
        and retrieve the result of the inference call when it is done."""

        url = QUEUE_URL_FORMAT + application
        if path:
            url += "/" + path.lstrip("/")

        if webhook_url is not None:
            url += "?" + urlencode({"modelrunner_webhook": webhook_url})

        headers = {}
        if hint is not None:
            headers["X-Modelrunner-Runner-Hint"] = hint

        if priority is not None:
            headers["X-Modelrunner-Queue-Priority"] = priority

        arguments = await self.transform_arguments(arguments)

        response = await _async_maybe_retry_request(
            self._client,
            "POST",
            url,
            json=arguments,
            timeout=self.default_timeout,
        )
        _raise_for_status(response)

        data = response.json()
        return AsyncRequestHandle(
            request_id=data["request_id"],
            response_url=data["response_url"],
            status_url=data["status_url"],
            cancel_url=data["cancel_url"],
            client=self._client,
        )

    async def subscribe(
        self,
        application: str,
        arguments: AnyJSON,
        *,
        path: str = "",
        hint: str | None = None,
        with_logs: bool = False,
        on_enqueue: Optional[callable[[Queued], None]] = None,
        on_queue_update: Optional[callable[[Status], None]] = None,
        priority: Optional[Priority] = None,
    ) -> AnyJSON:
        handle = await self.submit(
            application,
            arguments,
            path=path,
            hint=hint,
            priority=priority,
        )

        if on_enqueue is not None:
            on_enqueue(handle.request_id)

        if on_queue_update is not None:
            async for event in handle.iter_events(with_logs=with_logs):
                on_queue_update(event)

        return await handle.get()

    def get_handle(self, application: str, request_id: str) -> AsyncRequestHandle:
        return AsyncRequestHandle.from_request_id(self._client, application, request_id)

    async def status(
        self, application: str, request_id: str, *, with_logs: bool = False
    ) -> Status:
        handle = self.get_handle(application, request_id)
        return await handle.status(with_logs=with_logs)

    async def result(self, application: str, request_id: str) -> AnyJSON:
        handle = self.get_handle(application, request_id)
        return await handle.get()

    async def cancel(self, application: str, request_id: str) -> None:
        handle = self.get_handle(application, request_id)
        await handle.cancel()

    async def stream(
        self,
        application: str,
        arguments: AnyJSON,
        *,
        path: str = "/stream",
        timeout: float | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream the output of an application with the given arguments (which will be JSON serialized). This is only supported
        at a few select applications at the moment, so be sure to first consult with the documentation of individual applications
        to see if this is supported.

        The function will iterate over each event that is streamed from the server.
        """

        url = RUN_URL_FORMAT + application
        if path:
            url += "/" + path.lstrip("/")

        arguments = await self.transform_arguments(arguments)

        async with aconnect_sse(
            self._client,
            "POST",
            url,
            json=arguments,
            timeout=timeout,
        ) as events:
            async for event in events.aiter_sse():
                yield event.json()

    async def upload(
        self, data: str | bytes, content_type: str, file_name: str | None = None
    ) -> str:
        """Upload the given data blob and return the access URL."""

        if isinstance(data, str):
            data = data.encode("utf-8")

        if len(data) > MULTIPART_THRESHOLD:
            return await self._multipart_upload_pre_signed(
                data=data,
                content_type=content_type,
                file_name=file_name or _default_filename(content_type),
            )

        return await self._singlepart_upload_pre_signed(
            data=data,
            content_type=content_type,
            file_name=file_name or _default_filename(content_type),
        )

    async def _initiate_upload(
        self, file_name: str, content_type: str
    ) -> tuple[str, str]:
        resp = await _async_maybe_retry_request(
            self._client,
            "POST",
            f"{REST_URL}/storage/upload/initiate",
            json={"content_type": content_type, "file_name": file_name},
        )
        data = resp.json()
        return data["upload_url"], data["file_url"]

    async def _initiate_multipart_upload(
        self, file_name: str, content_type: str
    ) -> tuple[str, str]:
        resp = await _async_maybe_retry_request(
            self._client,
            "POST",
            f"{REST_URL}/storage/upload/initiate-multipart",
            json={"content_type": content_type, "file_name": file_name},
        )
        data = resp.json()
        return data["upload_url"], data["file_url"]

    async def _singlepart_upload_pre_signed(
        self, data: bytes, content_type: str, file_name: str
    ) -> str:
        upload_url, file_url = await self._initiate_upload(file_name, content_type)
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as c:
            resp = await c.put(
                upload_url, content=data, headers={"Content-Type": content_type}
            )
            _raise_for_status(resp)
        return file_url

    async def _multipart_upload_pre_signed(
        self, data: bytes, content_type: str, file_name: str
    ) -> str:
        upload_url, file_url = await self._initiate_multipart_upload(
            file_name, content_type
        )

        parsed = urlparse(upload_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        base_path = parsed.path
        query = f"?{parsed.query}" if parsed.query else ""

        parts = math.ceil(len(data) / MULTIPART_CHUNK_SIZE)
        part_results: list[dict[str, object]] = []

        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as c:
            for i in range(parts):
                start = i * MULTIPART_CHUNK_SIZE
                chunk = data[start : start + MULTIPART_CHUNK_SIZE]
                part_number = i + 1
                part_url = f"{origin}{base_path}/{part_number}{query}"

                resp = await c.put(part_url, content=chunk, timeout=None)
                _raise_for_status(resp)
                etag = resp.headers.get("ETag") or resp.headers.get("etag")
                if not etag:
                    raise ModelrunnerClientError(
                        "Missing ETag in multipart part upload response"
                    )

                part_results.append({"partNumber": part_number, "etag": etag})

            complete_url = f"{origin}{base_path}/complete{query}"
            resp = await c.post(complete_url, json={"parts": part_results})
            _raise_for_status(resp)

        return file_url

    async def transform_arguments(self, input: Any) -> Any:
        if isinstance(input, (list, tuple)):
            return [await self.transform_arguments(v) for v in input]
        if isinstance(input, dict):
            return {k: await self.transform_arguments(v) for k, v in input.items()}
        if isinstance(input, (bytes, bytearray, memoryview)):
            return await self.upload(bytes(input), "application/octet-stream")
        if isinstance(input, os.PathLike):
            return await self.upload_file(input)
        try:
            from PIL import Image as PILImage  # type: ignore

            if isinstance(input, PILImage.Image):
                return await self.upload_image(input)
        except Exception:
            pass
        return input

    async def upload_file(self, path: os.PathLike) -> str:
        """Upload a file from the local filesystem to the CDN and return the access URL."""

        mime_type, _ = mimetypes.guess_type(path)
        if mime_type is None:
            mime_type = "application/octet-stream"

        if os.path.getsize(path) > MULTIPART_THRESHOLD:
            return await self._multipart_upload_file_pre_signed(
                file_path=path,
                content_type=mime_type,
            )

        with open(path, "rb") as file:
            return await self.upload(
                file.read(), mime_type, file_name=os.path.basename(path)
            )

    async def upload_image(self, image: Image.Image, format: str = "jpeg") -> str:
        """Upload a pillow image object to the CDN and return the access URL."""

        with io.BytesIO() as buffer:
            image.save(buffer, format=format)
            return await self.upload(buffer.getvalue(), f"image/{format}")

    async def _multipart_upload_file_pre_signed(
        self, file_path: os.PathLike, content_type: str
    ) -> str:
        file_name = os.path.basename(file_path)
        upload_url, file_url = await self._initiate_multipart_upload(
            file_name, content_type
        )

        parsed = urlparse(upload_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        base_path = parsed.path
        query = f"?{parsed.query}" if parsed.query else ""

        size = os.path.getsize(file_path)
        parts = math.ceil(size / MULTIPART_CHUNK_SIZE)
        part_results: list[dict[str, object]] = []

        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as c:
            for part_number in range(1, parts + 1):
                with open(file_path, "rb") as f:
                    start = (part_number - 1) * MULTIPART_CHUNK_SIZE
                    f.seek(start)
                    chunk = f.read(MULTIPART_CHUNK_SIZE)

                part_url = f"{origin}{base_path}/{part_number}{query}"
                resp = await c.put(part_url, content=chunk, timeout=None)
                _raise_for_status(resp)
                etag = resp.headers.get("ETag") or resp.headers.get("etag")
                if not etag:
                    raise ModelrunnerClientError(
                        "Missing ETag in multipart part upload response"
                    )

                part_results.append({"partNumber": part_number, "etag": etag})

            complete_url = f"{origin}{base_path}/complete{query}"
            resp = await c.post(complete_url, json={"parts": part_results})
            _raise_for_status(resp)

        return file_url


@dataclass(frozen=True)
class SyncClient:
    key: str | None = field(default=None, repr=False)
    default_timeout: float = 120.0

    def _get_key(self) -> str:
        if self.key is None:
            return fetch_credentials()
        return self.key

    @cached_property
    def _client(self) -> httpx.Client:
        key = self._get_key()
        return httpx.Client(
            headers={
                "Authorization": f"Key {key}",
                "User-Agent": USER_AGENT,
            },
            timeout=self.default_timeout,
            follow_redirects=True,
        )

    def run(
        self,
        application: str,
        arguments: AnyJSON,
        *,
        path: str = "",
        timeout: float | None = None,
        hint: str | None = None,
    ) -> AnyJSON:
        """Run an application with the given arguments (which will be JSON serialized). The path parameter can be used to
        specify a subpath when applicable. This method will return the result of the inference call directly.
        """

        url = RUN_URL_FORMAT + application
        if path:
            url += "/" + path.lstrip("/")

        headers = {}
        if hint is not None:
            headers["X-Modelrunner-Runner-Hint"] = hint

        arguments = self.transform_arguments(arguments)

        response = _maybe_retry_request(
            self._client,
            "POST",
            url,
            json=arguments,
            timeout=timeout,
            headers=headers,
        )
        _raise_for_status(response)
        return response.json()

    def submit(
        self,
        application: str,
        arguments: AnyJSON,
        *,
        path: str = "",
        hint: str | None = None,
        webhook_url: str | None = None,
        priority: Optional[Priority] = None,
    ) -> SyncRequestHandle:
        """Submit an application with the given arguments (which will be JSON serialized). The path parameter can be used to
        specify a subpath when applicable. This method will return a handle to the request that can be used to check the status
        and retrieve the result of the inference call when it is done."""

        url = QUEUE_URL_FORMAT + application
        if path:
            url += "/" + path.lstrip("/")

        if webhook_url is not None:
            url += "?" + urlencode({"modelrunner_webhook": webhook_url})

        headers = {}
        if hint is not None:
            headers["X-Modelrunner-Runner-Hint"] = hint

        if priority is not None:
            headers["X-Modelrunner-Queue-Priority"] = priority

        arguments = self.transform_arguments(arguments)

        response = _maybe_retry_request(
            self._client,
            "POST",
            url,
            json=arguments,
            timeout=self.default_timeout,
            headers=headers,
        )
        _raise_for_status(response)

        data = response.json()
        return SyncRequestHandle(
            request_id=data["request_id"],
            response_url=data["response_url"],
            status_url=data["status_url"],
            cancel_url=data["cancel_url"],
            client=self._client,
        )

    def subscribe(
        self,
        application: str,
        arguments: AnyJSON,
        *,
        path: str = "",
        hint: str | None = None,
        with_logs: bool = False,
        on_enqueue: Optional[callable[[Queued], None]] = None,
        on_queue_update: Optional[callable[[Status], None]] = None,
        priority: Optional[Priority] = None,
    ) -> AnyJSON:
        handle = self.submit(
            application,
            arguments,
            path=path,
            hint=hint,
            priority=priority,
        )

        if on_enqueue is not None:
            on_enqueue(handle.request_id)

        if on_queue_update is not None:
            for event in handle.iter_events(with_logs=with_logs):
                on_queue_update(event)

        return handle.get()

    def get_handle(self, application: str, request_id: str) -> SyncRequestHandle:
        return SyncRequestHandle.from_request_id(self._client, application, request_id)

    def status(
        self, application: str, request_id: str, *, with_logs: bool = False
    ) -> Status:
        handle = self.get_handle(application, request_id)
        return handle.status(with_logs=with_logs)

    def result(self, application: str, request_id: str) -> AnyJSON:
        handle = self.get_handle(application, request_id)
        return handle.get()

    def cancel(self, application: str, request_id: str) -> None:
        handle = self.get_handle(application, request_id)
        handle.cancel()

    def stream(
        self,
        application: str,
        arguments: AnyJSON,
        *,
        path: str = "/stream",
        timeout: float | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream the output of an application with the given arguments (which will be JSON serialized). This is only supported
        at a few select applications at the moment, so be sure to first consult with the documentation of individual applications
        to see if this is supported.

        The function will iterate over each event that is streamed from the server.
        """

        url = RUN_URL_FORMAT + application
        if path:
            url += "/" + path.lstrip("/")

        arguments = self.transform_arguments(arguments)

        with connect_sse(
            self._client, "POST", url, json=arguments, timeout=timeout
        ) as events:
            for event in events.iter_sse():
                yield event.json()

    def upload(
        self, data: str | bytes, content_type: str, file_name: str | None = None
    ) -> str:
        """Upload the given data blob and return the access URL."""

        if isinstance(data, str):
            data = data.encode("utf-8")

        if len(data) > MULTIPART_THRESHOLD:
            return self._multipart_upload_pre_signed(
                data=data,
                content_type=content_type,
                file_name=file_name or _default_filename(content_type),
            )

        return self._singlepart_upload_pre_signed(
            data=data,
            content_type=content_type,
            file_name=file_name or _default_filename(content_type),
        )

    def _initiate_upload(self, file_name: str, content_type: str) -> tuple[str, str]:
        resp = _maybe_retry_request(
            self._client,
            "POST",
            f"{REST_URL}/storage/upload/initiate",
            json={"content_type": content_type, "file_name": file_name},
        )
        data = resp.json()
        return data["upload_url"], data["file_url"]

    def _initiate_multipart_upload(
        self, file_name: str, content_type: str
    ) -> tuple[str, str]:
        resp = _maybe_retry_request(
            self._client,
            "POST",
            f"{REST_URL}/storage/upload/initiate-multipart",
            json={"content_type": content_type, "file_name": file_name},
        )
        data = resp.json()
        return data["upload_url"], data["file_url"]

    def _singlepart_upload_pre_signed(
        self, data: bytes, content_type: str, file_name: str
    ) -> str:
        upload_url, file_url = self._initiate_upload(file_name, content_type)
        with httpx.Client(headers={"User-Agent": USER_AGENT}) as c:
            resp = c.put(
                upload_url, content=data, headers={"Content-Type": content_type}
            )
            _raise_for_status(resp)
        return file_url

    def _multipart_upload_pre_signed(
        self, data: bytes, content_type: str, file_name: str
    ) -> str:
        upload_url, file_url = self._initiate_multipart_upload(file_name, content_type)

        parsed = urlparse(upload_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        base_path = parsed.path
        query = f"?{parsed.query}" if parsed.query else ""

        parts = math.ceil(len(data) / MULTIPART_CHUNK_SIZE)
        part_results: list[dict[str, object]] = []

        with httpx.Client(headers={"User-Agent": USER_AGENT}) as c:
            for i in range(parts):
                start = i * MULTIPART_CHUNK_SIZE
                chunk = data[start : start + MULTIPART_CHUNK_SIZE]
                part_number = i + 1
                part_url = f"{origin}{base_path}/{part_number}{query}"

                resp = c.put(part_url, content=chunk, timeout=None)
                _raise_for_status(resp)
                etag = resp.headers.get("ETag") or resp.headers.get("etag")
                if not etag:
                    raise ModelrunnerClientError(
                        "Missing ETag in multipart part upload response"
                    )

                part_results.append({"partNumber": part_number, "etag": etag})

            complete_url = f"{origin}{base_path}/complete{query}"
            resp = c.post(complete_url, json={"parts": part_results})
            _raise_for_status(resp)

        return file_url

    def transform_arguments(self, input: Any) -> Any:
        if isinstance(input, (list, tuple)):
            return [self.transform_arguments(v) for v in input]
        if isinstance(input, dict):
            return {k: self.transform_arguments(v) for k, v in input.items()}
        if isinstance(input, (bytes, bytearray, memoryview)):
            return self.upload(bytes(input), "application/octet-stream")
        if isinstance(input, os.PathLike):
            return self.upload_file(input)
        try:
            from PIL import Image as PILImage  # type: ignore

            if isinstance(input, PILImage.Image):
                return self.upload_image(input)
        except Exception:
            pass
        return input

    def upload_file(self, path: os.PathLike) -> str:
        """Upload a file from the local filesystem to the CDN and return the access URL."""

        mime_type, _ = mimetypes.guess_type(path)
        if mime_type is None:
            mime_type = "application/octet-stream"

        if os.path.getsize(path) > MULTIPART_THRESHOLD:
            return self._multipart_upload_file_pre_signed(
                file_path=path,
                content_type=mime_type,
            )

        with open(path, "rb") as file:
            return self.upload(file.read(), mime_type, file_name=os.path.basename(path))

    def upload_image(self, image: Image.Image, format: str = "jpeg") -> str:
        """Upload a pillow image object to the CDN and return the access URL."""

        with io.BytesIO() as buffer:
            image.save(buffer, format=format)
            return self.upload(buffer.getvalue(), f"image/{format}")

    def _multipart_upload_file_pre_signed(
        self, file_path: os.PathLike, content_type: str
    ) -> str:
        file_name = os.path.basename(file_path)
        upload_url, file_url = self._initiate_multipart_upload(file_name, content_type)

        parsed = urlparse(upload_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        base_path = parsed.path
        query = f"?{parsed.query}" if parsed.query else ""

        size = os.path.getsize(file_path)
        parts = math.ceil(size / MULTIPART_CHUNK_SIZE)
        part_results: list[dict[str, object]] = []

        with httpx.Client(headers={"User-Agent": USER_AGENT}) as c:
            for part_number in range(1, parts + 1):
                with open(file_path, "rb") as f:
                    start = (part_number - 1) * MULTIPART_CHUNK_SIZE
                    f.seek(start)
                    chunk = f.read(MULTIPART_CHUNK_SIZE)

                part_url = f"{origin}{base_path}/{part_number}{query}"
                resp = c.put(part_url, content=chunk, timeout=None)
                _raise_for_status(resp)
                etag = resp.headers.get("ETag") or resp.headers.get("etag")
                if not etag:
                    raise ModelrunnerClientError(
                        "Missing ETag in multipart part upload response"
                    )

                part_results.append({"partNumber": part_number, "etag": etag})

            complete_url = f"{origin}{base_path}/complete{query}"
            resp = c.post(complete_url, json={"parts": part_results})
            _raise_for_status(resp)

        return file_url


def encode(data: str | bytes, content_type: str) -> str:
    """Encode the given data blob to a data URL with the specified content type."""
    if isinstance(data, str):
        data = data.encode("utf-8")

    return f"data:{content_type};base64,{base64.b64encode(data).decode()}"


def encode_file(path: os.PathLike) -> str:
    """Encode a file from the local filesystem to a data URL with the inferred content type."""
    mime_type, _ = mimetypes.guess_type(path)
    if mime_type is None:
        mime_type = "application/octet-stream"

    with open(path, "rb") as file:
        return encode(file.read(), mime_type)


def encode_image(image: Image.Image, format: str = "jpeg") -> str:
    """Encode a pillow image object to a data URL with the specified format."""
    with io.BytesIO() as buffer:
        image.save(buffer, format=format)
        return encode(buffer.getvalue(), f"image/{format}")
