"""Scoring a decision on a planted error, and telling the reviewer the outcome (ADR-003)."""

from __future__ import annotations

import sqlite3
from collections.abc import Container, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from reviewproof.models import Decision, DefectClass, EventType
from reviewproof.planted import PlantedLibrary
from reviewproof.records import ItemRef, Records
from reviewproof.store.eventlog import IntegrityError, item_events, voids_in_effect
from reviewproof.store.registry import commitment_of, read_entry
from reviewproof.store.schema import StoreError

_CHUNK = 500


class Outcome(str, Enum):
    CATCH = "catch"
    MISS = "miss"


def score(decision: Decision | str, edited_fields: Iterable[str], defect_field: str) -> Outcome:
    """A catch is a rejection, or an edit that changed the defect field. Anything else is a miss.

    An edit counts when the field changed. Whether the new value is right is not checked.
    """
    decision = Decision(decision)
    caught = decision is Decision.REJECT or (
        decision is Decision.EDIT and defect_field in edited_fields
    )
    return Outcome.CATCH if caught else Outcome.MISS


@dataclass(frozen=True)
class Feedback:
    """What to tell a reviewer about one planted error they decided on."""

    outcome: Outcome
    decision: Decision
    decided_at: str
    defect_class: DefectClass
    defect_field: str
    expected_decision: str
    #: The item as they saw it, so they can recognise it.
    payload: Mapping[str, Any] = field(repr=False)
    has_correct_value: bool = False
    correct_value: Any = field(default=None, repr=False)


def collect(
    records: Records, planted: sqlite3.Connection, library: PlantedLibrary, reviewer: str
) -> list[Feedback]:
    """Feedback not yet delivered to ``reviewer``, oldest first. Marks it delivered.

    All or nothing: if any part cannot be verified, none is delivered.
    """
    with records.log.transaction():
        found = []
        # Nothing is said about a voided planted error: its result measures nothing.
        voided = voids_in_effect(records.db, planted)
        for item_id in _awaiting(records, planted, reviewer, voided):
            events = {e["type"]: e for e in item_events(records.db, item_id)}
            submitted = events[EventType.SUBMITTED.value]
            decided = events[EventType.DECIDED.value]
            entry = read_entry(planted, item_id)
            if submitted["data"]["commitment"] != commitment_of(planted, entry):
                raise IntegrityError("the registry does not match the log; run rp verify")
            error = library.get(entry.planted_error_id)
            edited = (decided["data"] or {}).get("edited_fields", [])
            outcome = score(decided["decision"], edited, error.defect_field)
            records.append(
                EventType.FEEDBACK_SENT,
                ItemRef(item_id, submitted["system_id"]),
                reviewer,
                data={"outcome": outcome.value},
            )
            found.append(
                Feedback(
                    outcome=outcome,
                    decision=Decision(decided["decision"]),
                    decided_at=decided["ts_utc"],
                    defect_class=error.defect_class,
                    defect_field=error.defect_field,
                    expected_decision=error.expected_decision,
                    payload=error.payload,
                    has_correct_value=error.has_correct_value,
                    correct_value=error.correct_value,
                )
            )
    return found


def _awaiting(
    records: Records, planted: sqlite3.Connection, reviewer: str, voided: Container[str]
) -> list[str]:
    """Planted items this reviewer decided on, with no feedback sent, in decision order,
    leaving out voided planted errors.
    """
    try:
        ids = [
            row[0]
            for row in planted.execute(
                "SELECT r.item_id, r.planted_error_id FROM item_registry r JOIN planted_errors p "
                "ON p.planted_error_id = r.planted_error_id WHERE p.queue_id = ?",
                (records.queue,),
            )
            if row[1] not in voided
        ]
    except sqlite3.Error as error:
        raise StoreError(f"cannot read the item registry: {error}") from error
    decided: list[tuple[int, str]] = []
    sent: set[str] = set()
    for chunk in _chunks(ids):
        marks = ", ".join("?" * len(chunk))
        decided += [
            (row[0], row[1])
            for row in records.db.execute(
                "SELECT event_id, item_id FROM events WHERE type = ? AND reviewer_id = ? "
                f"AND item_id IN ({marks})",
                (EventType.DECIDED.value, reviewer, *chunk),
            )
        ]
        sent.update(
            row[0]
            for row in records.db.execute(
                f"SELECT item_id FROM events WHERE type = ? AND item_id IN ({marks})",
                (EventType.FEEDBACK_SENT.value, *chunk),
            )
        )
    return [item_id for _, item_id in sorted(decided) if item_id not in sent]


def _chunks(values: list[str]) -> Iterator[list[str]]:
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]
