"""The salted hashes that tie ``oversight.db`` to the restricted database (ADR-002).

Both salts are random per item and stored only in ``planted.db``.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Mapping
from typing import Any

from reviewproof.canonical import canonical_json, sha256_hex
from reviewproof.models import ItemKind

SALT_BYTES = 32

#: Everything about a planted error that is fixed once it is used, in hashing order. These are
#: the stored column values, so that any change to the stored text is detected. Retirement is
#: left out because it happens after use.
FINGERPRINT_COLUMNS = (
    "planted_error_id",
    "queue_id",
    "defect_class",
    "defect_field",
    "expected_decision",
    "payload",
    "correct_value",
    "notes",
    "reserved_target_id",
    "author",
    "imported_at",
    "approved_by",
    "approved_at",
    "second_approved_by",
    "second_approved_at",
)


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def planted_fingerprint(columns: Mapping[str, Any]) -> str:
    """Hash a ``planted_errors`` row as stored: content, scoring rules and approvals."""
    return sha256_hex(canonical_json([columns[name] for name in FINGERPRINT_COLUMNS]))


def planted_commitment(
    item_id: str,
    kind: ItemKind | str,
    planted_error_id: str | None,
    target_id: str,
    salt: bytes,
    fingerprint: str | None = None,
) -> str:
    """Fix an item's registry row, and any planted error behind it, in the log without
    revealing either.

    Every ``submitted`` event carries one, real and planted alike, so they cannot be told apart.
    A planted item needs the planted error's ``fingerprint``; a real item has none.
    """
    _check_salt(salt)
    kind = ItemKind(kind)
    if (kind is ItemKind.PLANTED) != (fingerprint is not None):
        raise ValueError("a planted item needs a fingerprint, and a real item must have none")
    record = [item_id, kind.value, planted_error_id, target_id, salt.hex(), fingerprint]
    return sha256_hex(canonical_json(record))


def qualification_commitment(
    item_id: str,
    session_id: str,
    position: int,
    planted_error_id: str | None,
    payload_text: str,
    salt: bytes,
    fingerprint: str | None,
) -> str:
    """Fix one item of a qualification session in the log, with its place in the answer key,
    without revealing whether it is defective.
    """
    _check_salt(salt)
    if (planted_error_id is None) != (fingerprint is None):
        raise ValueError("a defective item needs a fingerprint, and a correct one has none")
    record = [
        "qualification-item",
        item_id,
        session_id,
        position,
        planted_error_id,
        payload_text,
        salt.hex(),
        fingerprint,
    ]
    return sha256_hex(canonical_json(record))


def judgment_hash(judgment_text: str, salt: bytes) -> str:
    _check_salt(salt)
    return sha256_hex(canonical_json(["judgment", judgment_text, salt.hex()]))


def payload_hash(payload: Mapping[str, Any], payload_salt: bytes) -> str:
    """Hash an item's payload with that item's own salt.

    Without the salt, a planted error used twice would give the same hash twice and stand out.
    Matching a document to a logged hash therefore needs the salt from the registry.
    """
    _check_salt(payload_salt)
    return sha256_hex(canonical_json([payload_salt.hex(), _same_numbers(dict(payload))]))


def field_hashes(
    payload: Mapping[str, Any], payload_salt: bytes, names: Iterable[str] = ()
) -> dict[str, str]:
    """A salted hash for each top-level field, so that edits can be found without storing the
    item. ``names`` adds fields to include even when absent: every property the schema lists,
    so that leaving one out does not show.
    """
    _check_salt(payload_salt)
    found = {}
    for name in sorted({*names, *payload}):
        value = [_same_numbers(payload[name])] if name in payload else []
        found[name] = sha256_hex(canonical_json([payload_salt.hex(), "field", name, value]))
    return found


def _same_numbers(value: Any) -> Any:
    """Write 10.0 as 10. JavaScript and others make no difference between them, so a review
    screen that sends an item back must not appear to have changed it.
    """
    if isinstance(value, float) and value.is_integer() and abs(value) < 2**53:
        return int(value)
    if isinstance(value, list):
        return [_same_numbers(element) for element in value]
    if isinstance(value, Mapping):
        return {key: _same_numbers(element) for key, element in value.items()}
    return value


def _check_salt(salt: bytes) -> None:
    if not isinstance(salt, bytes) or len(salt) != SALT_BYTES:
        raise ValueError(f"a salt must be {SALT_BYTES} bytes")
