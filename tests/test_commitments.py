import pytest

from reviewproof.models import ItemKind
from reviewproof.store.commitments import new_salt, payload_hash, planted_commitment

SALT = bytes(range(32))
BASE = ["item-1", ItemKind.REAL, None, "rec-1", SALT]


def test_known_values():
    # Fixed vectors, calculated with hashlib and json alone from the format in ADR-002.
    # If these change, existing logs no longer verify.
    assert planted_commitment(*BASE) == (
        "4043688e5dfc894d630626f265d1ebc663478da12fa0973194dbb392c7d08ae6"
    )
    assert payload_hash({"a": 1}, SALT) == (
        "d79cc56b018853c58d10bdb11ecc56f5361dd99a25099bca143700b50b7d0666"
    )


@pytest.mark.parametrize(
    ("position", "value"),
    [(0, "item-2"), (1, ItemKind.PLANTED), (2, "PE-1"), (3, "rec-2"), (4, bytes(32))],
)
def test_every_part_changes_the_commitment(position, value):
    changed = list(BASE)
    changed[position] = value
    assert planted_commitment(*changed) != planted_commitment(*BASE)


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
