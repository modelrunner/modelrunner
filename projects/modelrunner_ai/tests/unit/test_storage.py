import json

import pytest

from modelrunner_ai.storage import (
    EXPIRATION_MAX_SECONDS,
    EXPIRATION_MIN_SECONDS,
    OBJECT_LIFECYCLE_PREFERENCE_HEADER,
    STORE_IO_HEADER,
    StorageSettings,
    expiration_duration_seconds,
    object_lifecycle_headers,
    store_io_headers,
)


@pytest.mark.parametrize(
    "expires_in, seconds",
    [
        ("1h", 3600),
        ("1d", 86400),
        ("7d", 604800),
        ("30d", 2592000),
        ("1y", 31536000),
        (3600, 3600),
        (EXPIRATION_MIN_SECONDS, EXPIRATION_MIN_SECONDS),
        (EXPIRATION_MAX_SECONDS, EXPIRATION_MAX_SECONDS),
    ],
)
def test_expiration_resolves_to_seconds(expires_in, seconds):
    assert expiration_duration_seconds(StorageSettings(expires_in)) == seconds


def test_never_resolves_to_no_expiration():
    """The API spells "no expiration" as a null, not as a very large duration --
    a hundred years is ~20x the maximum it accepts and would be rejected."""

    assert expiration_duration_seconds(StorageSettings("never")) is None


@pytest.mark.parametrize(
    "expires_in",
    [
        0,
        -1,
        # Outside the range the API accepts, so it is caught locally rather than
        # coming back as a 400.
        EXPIRATION_MIN_SECONDS - 1,
        EXPIRATION_MAX_SECONDS + 1,
        3153600000,  # what the JS client sends for "never"
        # bool is an int subclass; `expires_in=True` is never meant as 1 second.
        True,
        False,
        "1 hour",
        "2h",
        "",
        None,
        3.5,
    ],
)
def test_expiration_rejects_values_the_api_cannot_take(expires_in):
    with pytest.raises(ValueError):
        expiration_duration_seconds(StorageSettings(expires_in))


def test_expiration_rejects_immediate_rather_than_dropping_it():
    """The JS client maps "immediate" to no header at all, which keeps the
    object with the account default while the caller believes it expired. This
    client refuses it instead of repeating that silent no-op."""

    with pytest.raises(ValueError, match="immediate"):
        expiration_duration_seconds(StorageSettings("immediate"))


def test_expiration_rejects_a_bare_value_in_place_of_the_settings():
    with pytest.raises(ValueError, match="StorageSettings"):
        expiration_duration_seconds("1h")


def test_lifecycle_header_uses_the_documented_wire_shape():
    headers = object_lifecycle_headers(StorageSettings("1h"))

    assert set(headers) == {OBJECT_LIFECYCLE_PREFERENCE_HEADER}
    assert json.loads(headers[OBJECT_LIFECYCLE_PREFERENCE_HEADER]) == {
        "expiration_duration_seconds": 3600
    }


def test_lifecycle_header_is_omitted_without_a_preference():
    assert object_lifecycle_headers(None) == {}


def test_never_sends_an_explicit_null_rather_than_omitting_the_header():
    """Never is not the same as sending nothing: the null is what exempts a
    request from an account-wide media expiration, so the header has to go."""

    headers = object_lifecycle_headers(StorageSettings("never"))

    assert headers[OBJECT_LIFECYCLE_PREFERENCE_HEADER] == (
        '{"expiration_duration_seconds":null}'
    )


@pytest.mark.parametrize("store_io, value", [(False, "0"), (True, "1")])
def test_store_io_header(store_io, value):
    assert store_io_headers(store_io) == {STORE_IO_HEADER: value}


def test_store_io_header_is_omitted_without_an_opinion():
    assert store_io_headers(None) == {}


@pytest.mark.parametrize("store_io", ["0", 0, 1, "false"])
def test_store_io_rejects_non_bools(store_io):
    """A truthy string like "0" would otherwise opt *in* to storage while
    reading as an opt-out."""

    with pytest.raises(ValueError, match="store_io"):
        store_io_headers(store_io)
