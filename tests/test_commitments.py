import pytest

from reviewproof.models import ItemKind
from reviewproof.store.commitments import (
    FINGERPRINT_COLUMNS,
    new_salt,
    payload_hash,
    planted_commitment,
    planted_fingerprint,
)

SALT = bytes(range(32))
BASE = ["item-1", ItemKind.REAL, None, "rec-1", SALT]
PLANTED = ["item-1", ItemKind.PLANTED, "PE-1", "rec-1", SALT, "f" * 64]
ROW = dict(
    zip(
        FINGERPRINT_COLUMNS,
        [
            "PE-1",
            "q",
            "omission",
            "f",
            "reject",
            '{"a":1}',
            None,
            None,
            "rp-reserved-1",
            "author-1",
            "2026-11-26T10:00:00.000000Z",
            "qa-1",
            "2026-11-26T10:00:00.000000Z",
            None,
            None,
        ],
        strict=True,
    )
)


def test_known_values():
    # Fixed vectors, calculated with hashlib and json alone from the format in ADR-002.
    # If these change, existing logs no longer verify.
    assert planted_commitment(*BASE) == (
        "13da31ae0763b7fb8647d9edfb76a8734653f48b26910eabf42bd149ddf25614"
    )
    assert payload_hash({"a": 1}, SALT) == (
        "d79cc56b018853c58d10bdb11ecc56f5361dd99a25099bca143700b50b7d0666"
    )


def test_known_fingerprint():
    assert planted_fingerprint(ROW) == (
        "b649f783abe8e78e6ea8cfa70ddc6830135371cc1b211ef8c027530b54000c37"
    )


@pytest.mark.parametrize("column", FINGERPRINT_COLUMNS)
def test_every_column_changes_the_fingerprint(column):
    assert planted_fingerprint({**ROW, column: "changed"}) != planted_fingerprint(ROW)


@pytest.mark.parametrize(
    ("position", "value"),
    [(0, "item-2"), (2, "PE-2"), (3, "rec-2"), (4, bytes(32)), (5, "e" * 64)],
)
def test_every_part_changes_the_commitment(position, value):
    changed = list(PLANTED)
    changed[position] = value
    assert planted_commitment(*changed) != planted_commitment(*PLANTED)


@pytest.mark.parametrize(
    "arguments", [[*BASE, "f" * 64], PLANTED[:5]], ids=["real-with", "planted-without"]
)
def test_the_fingerprint_must_match_the_kind(arguments):
    with pytest.raises(ValueError, match="fingerprint"):
        planted_commitment(*arguments)


def test_the_same_payload_hashes_differently_per_item():
    payload = {"lot_number": "LOT-A00001"}
    assert payload_hash(payload, new_salt()) != payload_hash(payload, new_salt())


def test_payload_hash_is_repeatable_and_order_free():
    assert payload_hash({"a": 1.5, "b": 2}, SALT) == payload_hash({"b": 2, "a": 1.5}, SALT)
    assert payload_hash({"a": 1.5}, SALT) != payload_hash({"a": 1.6}, SALT)


@pytest.mark.parametrize("salt", [b"", b"short", "0" * 32, None])
def test_bad_salts_are_refused(salt):
    with pytest.raises(ValueError):
        payload_hash({}, salt)
    with pytest.raises(ValueError):
        planted_commitment("i", ItemKind.REAL, None, "t", salt)
