"""User-defined request metadata: a flat string map stored alongside a request.

It is a pure tagging side-channel. The API strips it before model-schema
validation, so it never reaches the model or the provider and is never merged
into the stored input.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

AnyJSON = Dict[str, Any]

#: Limits the API enforces. Mirrored here so a violation fails locally, with
#: every offending key reported at once, instead of costing a round trip.
REQUEST_METADATA_MAX_KEYS = 16
REQUEST_METADATA_KEY_MIN_LENGTH = 1
REQUEST_METADATA_KEY_MAX_LENGTH = 64
REQUEST_METADATA_VALUE_MAX_LENGTH = 512


def validate_metadata(metadata: Mapping[str, str]) -> None:
    """Validate a metadata map against the limits the API enforces.

    :raises ValueError: if any limit is violated. Every violation is collected
        into one message rather than raising on the first, so a caller fixing a
        batch of tags sees all of them at once.
    """

    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping of string keys to string values")

    problems = []

    if len(metadata) > REQUEST_METADATA_MAX_KEYS:
        problems.append(
            f"metadata must have at most {REQUEST_METADATA_MAX_KEYS} keys "
            f"(got {len(metadata)})"
        )

    for key, value in metadata.items():
        if not isinstance(key, str):
            problems.append(f"metadata key {key!r} must be a string")
            continue
        if not (
            REQUEST_METADATA_KEY_MIN_LENGTH
            <= len(key)
            <= REQUEST_METADATA_KEY_MAX_LENGTH
        ):
            problems.append(
                f"metadata key {key!r} must be between "
                f"{REQUEST_METADATA_KEY_MIN_LENGTH} and "
                f"{REQUEST_METADATA_KEY_MAX_LENGTH} characters"
            )
        if not isinstance(value, str):
            problems.append(
                f"metadata value for {key!r} must be a string "
                f"(got {type(value).__name__})"
            )
        elif len(value) > REQUEST_METADATA_VALUE_MAX_LENGTH:
            problems.append(
                f"metadata value for {key!r} must be at most "
                f"{REQUEST_METADATA_VALUE_MAX_LENGTH} characters (got {len(value)})"
            )

    if problems:
        raise ValueError("; ".join(problems))


def metadata_body_keys(metadata: Optional[Mapping[str, str]]) -> AnyJSON:
    """Build the reserved body key that tags a request.

    Returns an empty dict when there is no metadata, so the caller can merge it
    unconditionally. An explicitly empty map is valid and is sent as-is — that
    is how a caller clears tags rather than omitting them.

    Merge the result AFTER the storage transform, so nothing in the map is ever
    mistaken for a file to upload.

    :raises ValueError: if the map violates the API limits.
    """

    if metadata is None:
        return {}
    validate_metadata(metadata)
    return {"metadata": dict(metadata)}
