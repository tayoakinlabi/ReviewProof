"""The salted hashes that tie ``oversight.db`` to the restricted database (ADR-002).

Both salts are random per item and stored only in ``planted.db``.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
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


def payload_hash(payload: Mapping[str, Any], payload_salt: bytes) -> str:
    """Hash an item's payload with that item's own salt.

    Without the salt, a planted error used twice would give the same hash twice and stand out.
    Matching a document to a logged hash therefore needs the salt from the registry.
    """
    _check_salt(payload_salt)
    return sha256_hex(canonical_json([payload_salt.hex(), dict(payload)]))


def _check_salt(salt: bytes) -> None:
    if not isinstance(salt, bytes) or len(salt) != SALT_BYTES:
        raise ValueError(f"a salt must be {SALT_BYTES} bytes")
