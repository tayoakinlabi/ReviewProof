"""The item registry in ``planted.db``: which items are real, and what belongs to each (ADR-001).

Registry rows are written before the item appears in the log, and never change.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from reviewproof.models import ItemKind
from reviewproof.store.commitments import (
    FINGERPRINT_COLUMNS,
    planted_commitment,
    planted_fingerprint,
)
from reviewproof.store.schema import StoreError

TRACE_COLUMNS = ("step_id", "step_type", "inputs_hash", "outputs_hash", "ts_utc")
CONTEXT_COLUMNS = ("shown_sources", "shown_steps", "shown_fields")


@dataclass(frozen=True)
class RegistryEntry:
    item_id: str
    kind: ItemKind
    planted_error_id: str | None = field(repr=False)
    target_id: str = field(repr=False)
    salt: bytes = field(repr=False)
    payload_salt: bytes = field(repr=False)


class MissingEntryError(StoreError):
    """An item has no registry row."""


def read_entry(planted: sqlite3.Connection, item_id: str) -> RegistryEntry:
    """An item's registry row. A missing or unreadable row is an error, never a default."""
    try:
        row = planted.execute(
            "SELECT kind, planted_error_id, target_id, salt, payload_salt "
            "FROM item_registry WHERE item_id = ?",
            (item_id,),
        ).fetchone()
    except sqlite3.Error as error:
        raise StoreError(f"cannot read the item registry: {error}") from error
    if row is None:
        raise MissingEntryError("the item has no registry row")
    try:
        return RegistryEntry(item_id, ItemKind(row[0]), row[1], row[2], row[3], row[4])
    except ValueError as error:
        raise StoreError("the item's registry row is damaged") from error


def commitment_of(planted: sqlite3.Connection, entry: RegistryEntry) -> str:
    """What the log's commitment must equal if neither the row nor any planted error behind it
    has changed.
    """
    try:
        fingerprint = None
        if entry.planted_error_id is not None:
            row = planted.execute(
                f"SELECT {', '.join(FINGERPRINT_COLUMNS)} FROM planted_errors "
                "WHERE planted_error_id = ?",
                (entry.planted_error_id,),
            ).fetchone()
            if row is None:
                raise StoreError("the registry row refers to something that is gone")
            fingerprint = planted_fingerprint(dict(zip(FINGERPRINT_COLUMNS, row, strict=True)))
        return planted_commitment(
            entry.item_id,
            entry.kind,
            entry.planted_error_id,
            entry.target_id,
            entry.salt,
            fingerprint,
        )
    except sqlite3.Error as error:
        raise StoreError(f"cannot read the registry: {error}") from error
    except (ValueError, TypeError) as error:
        raise StoreError("the item's registry row is damaged") from error


def add_real_item(
    planted: sqlite3.Connection,
    item_id: str,
    target_id: str,
    salt: bytes,
    payload_salt: bytes,
    trace: Sequence[Mapping[str, Any]],
) -> None:
    """Write a real item's registry row and trace records, together or not at all."""
    if planted.in_transaction:
        raise StoreError("the restricted database is already in a transaction")
    try:
        planted.execute("BEGIN IMMEDIATE")
        planted.execute(
            "INSERT INTO item_registry (item_id, kind, target_id, salt, payload_salt) "
            "VALUES (?, ?, ?, ?, ?)",
            (item_id, ItemKind.REAL.value, target_id, salt, payload_salt),
        )
        for step in trace:
            planted.execute(
                f"INSERT INTO traces (item_id, {', '.join(TRACE_COLUMNS)}) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, *(step[column] for column in TRACE_COLUMNS)),
            )
        planted.execute("COMMIT")
    except sqlite3.Error as error:
        raise StoreError(f"cannot write the item registry: {error}") from error
    finally:
        if planted.in_transaction:
            planted.execute("ROLLBACK")
