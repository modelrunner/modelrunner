"""Data-retention options: how long generated media is kept, and whether the
request payload is stored at all.

Both travel as request *headers* rather than as reserved body keys, so unlike
metadata and webhooks they never touch the arguments and cannot collide with a
model's input schema.

The per-request value overrides the account-wide default set in the dashboard.
It does not widen it: an account restricted to a shorter retention stays
restricted, so treat a longer ``expires_in`` here as a request, not a promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Union

try:  # pragma: no cover - Literal is in typing from 3.8 on
    from typing import Literal
except ImportError:  # pragma: no cover
    from typing_extensions import Literal  # type: ignore

import json

#: Controls how long generated files (images, videos, etc.) are kept.
OBJECT_LIFECYCLE_PREFERENCE_HEADER = "x-modelrunner-object-lifecycle-preference"

#: Controls whether the request and response payloads are stored at all.
STORE_IO_HEADER = "x-modelrunner-store-io"

NamedExpiration = Literal["never", "1h", "1d", "7d", "30d", "1y"]
ObjectExpiration = Union[NamedExpiration, int]

#: The named windows, in seconds. ``never`` is a hundred years rather than a
#: sentinel because the API takes a duration and has no "keep forever" value;
#: this matches what the JS client sends.
EXPIRATION_VALUES: Dict[str, int] = {
    "never": 3153600000,  # 100 years
    "1h": 3600,
    "1d": 86400,
    "7d": 604800,
    "30d": 2592000,
    "1y": 31536000,
}


@dataclass(frozen=True)
class StorageSettings:
    """How long generated objects stay available before they expire.

    :param expires_in: one of ``"never"``, ``"1h"``, ``"1d"``, ``"7d"``,
        ``"30d"``, ``"1y"``, or a number of seconds.
    """

    expires_in: ObjectExpiration


def expiration_duration_seconds(lifecycle: StorageSettings) -> int:
    """Resolve a :class:`StorageSettings` to a duration in seconds.

    :raises ValueError: if ``expires_in`` is not a known window or a positive
        number of seconds.
    """

    if not isinstance(lifecycle, StorageSettings):
        raise ValueError(
            f"lifecycle must be a StorageSettings (got {type(lifecycle).__name__})"
        )

    expires_in = lifecycle.expires_in

    # bool is a subclass of int, and `expires_in=True` is never meant as one
    # second, so it is rejected rather than quietly accepted.
    if isinstance(expires_in, bool):
        raise ValueError("expires_in must be a duration or a named window, not a bool")

    if isinstance(expires_in, int):
        if expires_in <= 0:
            raise ValueError(
                f"expires_in must be a positive number of seconds (got {expires_in})"
            )
        return expires_in

    if isinstance(expires_in, str):
        # NOTE: the JS client also accepts "immediate", which it maps to
        # undefined -- dropping the header entirely, so the object is kept with
        # the account default while the caller believes it expires at once.
        # Rejecting it here is deliberate: a retention option that silently
        # does nothing is the one failure mode worth being loud about.
        if expires_in == "immediate":
            raise ValueError(
                "expires_in does not support 'immediate'; the API takes a "
                "duration, and there is no wire value that expires an object "
                "on arrival. Pass a short duration in seconds instead."
            )
        if expires_in not in EXPIRATION_VALUES:
            raise ValueError(
                f"expires_in must be a number of seconds or one of "
                f"{', '.join(sorted(EXPIRATION_VALUES))} (got {expires_in!r})"
            )
        return EXPIRATION_VALUES[expires_in]

    raise ValueError(
        "expires_in must be a number of seconds or a named window "
        f"(got {type(expires_in).__name__})"
    )


def object_lifecycle_headers(
    lifecycle: Optional[StorageSettings],
) -> Dict[str, str]:
    """Build the media-expiry header.

    Returns an empty dict when there is no preference, so the caller can merge
    it unconditionally.

    :raises ValueError: if the settings are not valid.
    """

    if lifecycle is None:
        return {}

    return {
        OBJECT_LIFECYCLE_PREFERENCE_HEADER: json.dumps(
            {"expiration_duration_seconds": expiration_duration_seconds(lifecycle)},
            # Compact, so the value is byte-identical to what the JS client's
            # JSON.stringify sends.
            separators=(",", ":"),
        )
    }


def store_io_headers(store_io: Optional[bool]) -> Dict[str, str]:
    """Build the payload-storage header.

    Returns an empty dict when the caller has no opinion, leaving the account
    default in place.

    :raises ValueError: if ``store_io`` is not a bool.
    """

    if store_io is None:
        return {}

    if not isinstance(store_io, bool):
        raise ValueError(f"store_io must be a bool (got {type(store_io).__name__})")

    # NOTE: only "0" is documented. "1" is the natural encoding of the opt-in
    # direction -- it is what any `header != "0"` or bool parse reads as "store"
    # -- and without it an account that opts out by default could never opt a
    # single request back in. Confirm against the API before relying on the
    # opt-in direction; see modelrunner/modelrunner#2.
    return {STORE_IO_HEADER: "1" if store_io else "0"}
