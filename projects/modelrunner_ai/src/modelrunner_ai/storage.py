"""Data-retention options: how long generated media is kept, and whether the
request payload is stored at all.

Both travel as request *headers* rather than as reserved body keys, so unlike
metadata and webhooks they never touch the arguments and cannot collide with a
model's input schema.

The per-request value always wins over the account-wide default set in the
dashboard, in both directions -- including ``expires_in="never"``, which is how
a single request is exempted from an account-wide media expiration.
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

#: Bounds the API enforces on ``expiration_duration_seconds``. Mirrored here so
#: a violation fails locally, with the offending value named, instead of costing
#: a round trip and coming back as a 400.
EXPIRATION_MIN_SECONDS = 60
EXPIRATION_MAX_SECONDS = 157680000  # 5 years

#: The named windows, in seconds. ``never`` is ``None`` -- it serializes to a
#: JSON null, which is the API's "no expiration", and is also how a single
#: request opts out of an account-wide media expiration.
#:
#: NOTE: the JS client instead sends 3153600000 (100 years) for "never". That is
#: roughly twenty times EXPIRATION_MAX_SECONDS, so it is rejected rather than
#: treated as forever; see modelrunner/modelrunner-js#8.
EXPIRATION_VALUES: Dict[str, Optional[int]] = {
    "never": None,
    "1h": 3600,
    "1d": 86400,
    "7d": 604800,
    "30d": 2592000,
    "1y": 31536000,
}


@dataclass(frozen=True)
class StorageSettings:
    """How long generated objects stay available before they expire.

    The countdown starts when the request **finishes**, not when it is
    submitted, so a short window still gives you that long after the output
    exists.

    :param expires_in: one of ``"never"``, ``"1h"``, ``"1d"``, ``"7d"``,
        ``"30d"``, ``"1y"``, or a number of seconds between
        ``EXPIRATION_MIN_SECONDS`` and ``EXPIRATION_MAX_SECONDS``.
    """

    expires_in: ObjectExpiration


def expiration_duration_seconds(lifecycle: StorageSettings) -> Optional[int]:
    """Resolve a :class:`StorageSettings` to a duration in seconds.

    Returns ``None`` for ``"never"``, which is the API's "no expiration" and
    serializes to a JSON null rather than to a very large number.

    :raises ValueError: if ``expires_in`` is not a known window or a duration
        the API accepts.
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
        if not (EXPIRATION_MIN_SECONDS <= expires_in <= EXPIRATION_MAX_SECONDS):
            raise ValueError(
                f"expires_in must be between {EXPIRATION_MIN_SECONDS} and "
                f"{EXPIRATION_MAX_SECONDS} seconds (got {expires_in}); pass "
                f'"never" for no expiration'
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
                f"on arrival. The shortest it accepts is "
                f"{EXPIRATION_MIN_SECONDS} seconds."
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
