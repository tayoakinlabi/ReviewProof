from datetime import datetime, timedelta, timezone

import pytest

from reviewproof.canonical import canonical_json, format_timestamp, sha256_hex, utc_now


def test_canonical_form_is_fixed():
    value = {"b": [1, True, None], "a": "µg – 5", "c": {"z": 1.5, "y": ""}}
    expected = '{"a":"µg – 5","b":[1,true,null],"c":{"y":"","z":1.5}}'.encode()
    assert canonical_json(value) == expected


def test_known_hash():
    # A fixed vector: if this changes, existing logs would no longer verify.
    assert sha256_hex(canonical_json({"a": 1})) == (
        "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
    )


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), {1: "a"}, (1, 2), {"a": {1, 2}}, b"bytes", [object()]],
)
def test_values_that_would_not_round_trip_are_rejected(value):
    with pytest.raises((TypeError, ValueError)):
        canonical_json(value)


def test_timestamp_is_converted_to_utc():
    moment = datetime(2026, 11, 26, 1, 30, 0, 5, tzinfo=timezone(timedelta(hours=2)))
    assert format_timestamp(moment) == "2026-11-25T23:30:00.000005Z"


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError):
        format_timestamp(datetime(2026, 11, 26))


def test_now_uses_the_fixed_format():
    assert datetime.strptime(utc_now(), "%Y-%m-%dT%H:%M:%S.%fZ")
