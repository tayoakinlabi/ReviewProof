"""Void records, and which of them the log has put in effect (ADR-008).

``planted.db`` holds requests, confirmations and cancellations. **Only the log puts a void in
effect:** it holds one ``planted_voided`` event with a commitment to exactly that record. Every
such event must match one unchanged record, or nothing can be trusted. A record with no event
has no effect.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from reviewproof.models import VoidReason
from reviewproof.store.commitments import void_commitment
from reviewproof.store.registry import fingerprint_of
from reviewproof.store.schema import StoreError

_MISMATCH = "the void record does not match the log"


@dataclass(frozen=True)
class Void:
    """One request to void a planted error, and what became of it. Secret: it names a planted
    error. Whether it is in effect is for the log to say, not this record.
    """

    void_id: str
    planted_error_id: str = field(repr=False)
    reason: VoidReason
    explanation: str = field(repr=False)
    reference: str = field(repr=False)
    requested_by: str
    requested_at: str
    salt: bytes = field(repr=False)
    confirmed_by: str | None = None
    confirmed_at: str | None = None
    cancelled_by: str | None = None
    cancelled_at: str | None = None


def read_voids(planted: sqlite3.Connection) -> list[Void]:
    """Every request, oldest first. A database not yet upgraded has none."""
    try:
        if not planted.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'void_requests'"
        ).fetchone():
            return []
        rows = planted.execute(
            "SELECT r.void_id, r.planted_error_id, r.reason, r.explanation, r.reference, "
            "r.requested_by, r.requested_at, r.salt, c.confirmed_by, c.confirmed_at, "
            "x.cancelled_by, x.cancelled_at FROM void_requests r "
            "LEFT JOIN void_confirmations c ON c.void_id = r.void_id "
            "LEFT JOIN void_cancellations x ON x.void_id = r.void_id ORDER BY r.rowid"
        ).fetchall()
    except sqlite3.Error as error:
        raise StoreError(f"cannot read the void records: {error}") from error
    try:
        return [Void(row[0], row[1], VoidReason(row[2]), *tuple(row)[3:]) for row in rows]
    except ValueError as error:
        raise StoreError("a void record is damaged") from error


def commitment_for(planted: sqlite3.Connection, void: Void) -> str:
    """The commitment to a confirmed record, with the planted error as it is stored now."""
    record = [
        void.void_id,
        void.planted_error_id,
        void.reason.value,
        void.explanation,
        void.reference,
        void.requested_by,
        void.requested_at,
        void.confirmed_by,
        void.confirmed_at,
    ]
    return void_commitment(record, void.salt, fingerprint_of(planted, void.planted_error_id))


def match_voids(
    planted: sqlite3.Connection, events: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Void], list[str]]:
    """Match each ``planted_voided`` event, in order and with data parsed, to its record.

    Returns the voids in effect, by planted error ID, and what is wrong. No message names a
    planted error.
    """
    if not events:
        return {}, []
    in_effect: dict[str, Void] = {}
    errors: list[str] = []
    try:
        records = read_voids(planted)
    except StoreError:
        return {}, [f"event {e['event_id']}: the void records cannot be read" for e in events]
    unmatched: dict[str, Void] = {}
    for void in records:
        if void.confirmed_by is not None and void.cancelled_by is None:
            try:
                unmatched[commitment_for(planted, void)] = void
            except (StoreError, sqlite3.Error, ValueError, TypeError):
                continue  # a damaged record matches no event, which is reported below
    for event in events:
        where = f"event {event['event_id']}"
        data = event["data"]
        commitment = data.get("commitment") if set(data) == {"commitment"} else None
        # Taken out, so that two events can never share one record.
        void = unmatched.pop(commitment, None) if isinstance(commitment, str) else None
        about_an_item = any(event[name] for name in ("item_id", "reviewer_id", "decision"))
        if void is None or about_an_item or void.planted_error_id in in_effect:
            errors.append(f"{where}: {_MISMATCH}")
            continue
        queue = planted.execute(
            "SELECT queue_id FROM planted_errors WHERE planted_error_id = ?",
            (void.planted_error_id,),
        ).fetchone()
        if queue is None or queue[0] != event["queue_id"]:
            errors.append(f"{where}: {_MISMATCH}")
            continue
        in_effect[void.planted_error_id] = void
    return in_effect, errors
