"""Webhook support: attaching a webhook to a request, and verifying deliveries.

Signing follows Standard Webhooks (https://www.standardwebhooks.com), the same
scheme Stripe, GitHub and Slack use, so this module needs nothing beyond the
standard library.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

Headers = Mapping[str, Any]
AnyJSON = Dict[str, Any]

#: The lifecycle events a request can notify a webhook about.
#:
#: ``start`` is best effort: a fast request can go straight from ``IN_QUEUE`` to
#: ``COMPLETED`` between two provider polls, in which case only ``completed`` is
#: delivered. Never block waiting for ``start``.
WEBHOOK_EVENTS = ("start", "completed")

#: Longest webhook URL the API accepts.
WEBHOOK_URL_MAX_LENGTH = 2048

WEBHOOK_ID_HEADER = "webhook-id"
WEBHOOK_TIMESTAMP_HEADER = "webhook-timestamp"
WEBHOOK_SIGNATURE_HEADER = "webhook-signature"

#: How far the ``webhook-timestamp`` may be from now before a delivery is
#: rejected as a replay. The API recomputes the timestamp on every attempt, so a
#: legitimate retry two hours later still arrives inside the window.
DEFAULT_TOLERANCE_SECONDS = 300

_SECRET_PREFIX = "whsec_"
_SIGNATURE_PREFIX = "v1,"


class WebhookVerificationError(Exception):
    """Raised when a delivery cannot be attributed to the signing secret.

    Covers every failure mode — a missing or malformed header, a timestamp
    outside the tolerance window, a signature that does not match. Treat them
    all the same way: respond 401 and do not process the body. Never branch on
    the message.
    """


def webhook_body_keys(
    webhook_url: Optional[str],
    webhook_events: Optional[Sequence[str]] = None,
) -> AnyJSON:
    """Build the reserved body keys that attach a webhook to a request.

    Returns an empty dict when there is no webhook, so the caller can merge it
    unconditionally. ``webhook_events_filter`` is omitted when no events are
    given, letting the API apply its own default of ``["completed"]``.

    Deliberately does **not** check the URL scheme. ``https`` is the rule on a
    normal deployment, but the API relaxes it under
    ``WEBHOOK_ALLOW_INSECURE_URLS`` so a developer can point a request at a
    local receiver — refusing ``http`` here would make that impossible through
    the client while raw HTTP still worked. Scheme and SSRF rules stay
    server-side, where they are also re-checked against the resolved address at
    delivery time.

    :raises ValueError: if the url or the event filter violates the API limits,
        or if events are given without a url — a combination the API rejects.
    """

    if webhook_url is None:
        if webhook_events is not None:
            raise ValueError("webhook_events requires a webhook_url")
        return {}

    if not isinstance(webhook_url, str):
        raise ValueError("webhook_url must be a string")
    if len(webhook_url) > WEBHOOK_URL_MAX_LENGTH:
        raise ValueError(
            f"webhook_url must be at most {WEBHOOK_URL_MAX_LENGTH} characters"
        )

    body: AnyJSON = {"webhook": webhook_url}

    if webhook_events is not None:
        if not isinstance(webhook_events, (list, tuple)):
            raise ValueError("webhook_events must be a list of event names")
        # Deduplicate while keeping the caller's order.
        events: List[str] = []
        for event in webhook_events:
            if event not in WEBHOOK_EVENTS:
                raise ValueError(
                    "webhook_events must only contain "
                    + " or ".join(WEBHOOK_EVENTS)
                )
            if event not in events:
                events.append(event)
        if not events:
            raise ValueError("webhook_events must contain at least one event")
        body["webhook_events_filter"] = events

    return body


def _read_header(headers: Headers, name: str) -> str:
    value = None
    getter = getattr(headers, "get", None)
    if callable(getter):
        # httpx.Headers, werkzeug and starlette are all case-insensitive.
        value = getter(name)
    if value is None:
        for key in headers:
            if isinstance(key, str) and key.lower() == name:
                value = headers[key]
                break
    if isinstance(value, (list, tuple)):
        # A repeated signature header is ambiguous, and guessing which copy to
        # trust is exactly the kind of decision that turns into a bypass.
        raise WebhookVerificationError(f"The {name} header was sent more than once.")
    if value is None or value == "":
        raise WebhookVerificationError(f"Missing the {name} header.")
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _secret_to_key(secret: str) -> bytes:
    encoded = (
        secret[len(_SECRET_PREFIX) :]
        if secret.startswith(_SECRET_PREFIX)
        else secret
    )
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WebhookVerificationError(
            "The signing secret is not valid base64."
        ) from exc


def verify_webhook(
    secret: Union[str, Sequence[str]],
    headers: Headers,
    body: Union[str, bytes],
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> AnyJSON:
    """Verify a webhook delivery and return its parsed payload.

    HMAC-SHA256 over ``{webhook-id}.{webhook-timestamp}.{body}``, compared
    against the space-delimited list in ``webhook-signature``. The list carries
    more than one signature while a secret is being rotated, and matching
    **any** entry is correct — that is what gives you a 24 hour window to deploy
    a rotated secret.

    ``body`` must be the **raw** request body. The signature covers the
    delivered bytes, so a body that has been parsed and re-serialized will not
    verify; read it before any JSON body parser runs (``await request.body()``
    in FastAPI, ``request.get_data()`` in Flask).

    :param secret: the ``whsec_…`` secret, or several to try while rolling from
        one to the next.
    :param headers: the delivery's headers, looked up case-insensitively.
    :param body: the raw request body.
    :param tolerance_seconds: how far the timestamp may be from now.
    :returns: the delivered payload.
    :raises WebhookVerificationError: if the delivery cannot be verified.
    """

    secrets = [secret] if isinstance(secret, str) else list(secret)
    if not secrets:
        raise WebhookVerificationError(
            "A signing secret is required to verify a webhook."
        )

    webhook_id = _read_header(headers, WEBHOOK_ID_HEADER)
    timestamp = _read_header(headers, WEBHOOK_TIMESTAMP_HEADER)
    signature_header = _read_header(headers, WEBHOOK_SIGNATURE_HEADER)

    try:
        timestamp_seconds = int(timestamp)
    except ValueError as exc:
        raise WebhookVerificationError(
            "The webhook-timestamp header is not a number."
        ) from exc
    if abs(time.time() - timestamp_seconds) > tolerance_seconds:
        raise WebhookVerificationError(
            f"The webhook-timestamp header is outside the "
            f"{tolerance_seconds}s tolerance window."
        )

    # Every entry is attacker-supplied, so a malformed one is a non-match rather
    # than an error: it must not be able to short-circuit the valid entry
    # alongside it.
    signatures: List[bytes] = []
    for entry in signature_header.split(" "):
        if not entry.startswith(_SIGNATURE_PREFIX):
            continue
        try:
            signatures.append(
                base64.b64decode(entry[len(_SIGNATURE_PREFIX) :], validate=True)
            )
        except (binascii.Error, ValueError):
            continue
    if not signatures:
        raise WebhookVerificationError(
            "The webhook-signature header carries no v1 signature."
        )

    payload = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    # Built as bytes rather than as a string so a body that is not valid UTF-8
    # still hashes to what the sender hashed.
    signed_content = f"{webhook_id}.{timestamp}.".encode("utf-8") + payload

    verified = False
    for candidate in secrets:
        expected = hmac.new(
            _secret_to_key(candidate), signed_content, hashlib.sha256
        ).digest()
        for signature in signatures:
            if hmac.compare_digest(expected, signature):
                verified = True
                break
        if verified:
            break
    if not verified:
        raise WebhookVerificationError(
            "No signature in the webhook-signature header matches the signing secret."
        )

    try:
        return json.loads(payload)
    except ValueError as exc:
        raise WebhookVerificationError(
            "The webhook body is not valid JSON, despite a valid signature."
        ) from exc
