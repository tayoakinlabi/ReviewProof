"""The two salted hashes that tie ``oversight.db`` to the restricted registry (ADR-002).

Both salts are random per item and stored only in ``planted.db``.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any

from reviewproof.canonical import canonical_json, sha256_hex
from reviewproof.models import ItemKind

SALT_BYTES = 32


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def planted_commitment(
    item_id: str,
    kind: ItemKind | str,
    planted_error_id: str | None,
    target_id: str,
    salt: bytes,
) -> str:
    """Fix an item's registry row in the log without revealing it.

    Every ``submitted`` event carries one, real and planted alike, so they cannot be told apart.
    """
    _check_salt(salt)
    record = [item_id, ItemKind(kind).value, planted_error_id, target_id, salt.hex()]
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
