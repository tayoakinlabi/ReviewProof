"""Turn the event log into one record per item (step 2.1).

Everything comes from the event log, plus the restricted database for which items are planted,
and only after ``verify`` has confirmed that the log is intact and that every registry row and
planted error still matches what the log committed to. Evidence that does not verify is never
turned into scores.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from reviewproof.feedback import Outcome, score
from reviewproof.models import Decision, DefectClass, EventType
from reviewproof.store.eventlog import Head, VerifyResult, verify
from reviewproof.store.schema import StoreError, open_readonly

_TIMESTAMP = "%Y-%m-%dT%H:%M:%S.%fZ"


class EvidenceError(Exception):
    """The evidence did not verify, so nothing was calculated."""

    def __init__(self, result: VerifyResult) -> None:
        super().__init__("the evidence did not verify; run rp verify")
        self.result = result


def parse_time(text: str) -> datetime:
    return datetime.strptime(text, _TIMESTAMP).replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Context:
    """One version of an AI system, as registered in the log."""

    system_id: str
    system: str
    model_id: str
    model_version: str
    prompt_version: str
    tool_schema_hash: str

    @property
    def label(self) -> str:
        return (
            f"{self.system}: model {self.model_id} {self.model_version}, "
            f"prompt {self.prompt_version}"
        )


@dataclass(frozen=True)
class Showing:
    event_id: int
    reviewer: str
    at: datetime


@dataclass(frozen=True)
class ItemRecord:
    item_id: str
    queue: str
    system_id: str
    submitted_at: datetime
    planted_error_id: str | None = None
    defect_class: DefectClass | None = None
    defect_field: str | None = None
    showings: tuple[Showing, ...] = ()
    decision: Decision | None = None
    #: Who decided.
    reviewer: str | None = None
    decided_at: datetime | None = None
    decided_event: int | None = None
    edited_fields: tuple[str, ...] = ()
    executed: bool = False
    execute_failed: bool = False
    blocked_reasons: tuple[str, ...] = ()
    #: For a decided planted item: was it the decider's first sight of that planted error?
    first_sight: bool | None = None

    @property
    def planted(self) -> bool:
        return self.planted_error_id is not None

    @property
    def decided(self) -> bool:
        return self.decision is not None

    @property
    def first_shown_at(self) -> datetime | None:
        """When the decider's screen first showed it."""
        return next((s.at for s in self.showings if s.reviewer == self.reviewer), None)

    @property
    def dwell_ms(self) -> int | None:
        """Shown to decided, from the decider's first showing."""
        start = self.first_shown_at
        if self.decided_at is None or start is None:
            return None
        return round((self.decided_at - start).total_seconds() * 1000)

    @property
    def outcome(self) -> Outcome | None:
        """Catch or miss, for a decided planted item. Counts whether or not the host ever
        asked to execute it.
        """
        if not self.planted or self.decision is None:
            return None
        return score(self.decision, self.edited_fields, self.defect_field)


@dataclass(frozen=True)
class Refusal:
    """The guard refused to execute for a reason other than the item being planted."""

    queue: str
    at: datetime
    reason: str


@dataclass(frozen=True)
class Projection:
    records: tuple[ItemRecord, ...]
    contexts: dict[str, Context] = field(default_factory=dict)
    refusals: tuple[Refusal, ...] = ()
    head: Head = Head(0, "0" * 64)


def load(db: str | Path, planted_db: str | Path, expected_head: Head | None = None) -> Projection:
    """Verify the evidence, then project it. Both databases are opened read-only."""
    with (
        closing(open_readonly(db, "oversight")) as oversight,
        closing(open_readonly(planted_db, "planted")) as planted,
    ):
        return project(oversight, planted, expected_head)


def project(
    oversight: sqlite3.Connection,
    planted: sqlite3.Connection,
    expected_head: Head | None = None,
) -> Projection:
    # One read transaction, so that what is projected is exactly what was verified, even while
    # the application keeps writing.
    try:
        oversight.execute("BEGIN")
        try:
            result = verify(oversight, planted, expected_head)
            if not result.ok:
                raise EvidenceError(result)
            events = [
                dict(row)
                for row in oversight.execute(
                    "SELECT event_id, type, ts_utc, system_id, queue_id, item_id, "
                    "reviewer_id, decision, data FROM events ORDER BY event_id"
                )
            ]
        finally:
            oversight.execute("COMMIT")
        return _fold(events, _planted_items(planted), result.head)
    except sqlite3.Error as error:
        raise StoreError(f"cannot read the evidence: {error}") from error


def _planted_items(planted: sqlite3.Connection) -> dict[str, tuple[str, DefectClass, str]]:
    rows = planted.execute(
        "SELECT r.item_id, p.planted_error_id, p.defect_class, p.defect_field "
        "FROM item_registry r JOIN planted_errors p ON p.planted_error_id = r.planted_error_id"
    )
    return {row[0]: (row[1], DefectClass(row[2]), row[3]) for row in rows}


def _fold(
    events: Iterable[dict],
    planted: dict[str, tuple[str, DefectClass, str]],
    head: Head,
) -> Projection:
    items: dict[str, dict] = {}
    contexts: dict[str, Context] = {}
    refusals: list[Refusal] = []
    # (reviewer, planted error) -> the item where they first saw it
    first_seen: dict[tuple[str, str], str] = {}

    for event in events:
        kind = EventType(event["type"])
        data = json.loads(event["data"])
        at = parse_time(event["ts_utc"])
        item_id = event["item_id"]
        if kind is EventType.CONTEXT_REGISTERED:
            contexts[event["system_id"]] = Context(
                event["system_id"],
                data["system"],
                data["model_id"],
                data["model_version"],
                data["prompt_version"],
                data["tool_schema_hash"],
            )
        elif kind is EventType.SUBMITTED:
            entry = dict(
                item_id=item_id,
                queue=event["queue_id"],
                system_id=event["system_id"],
                submitted_at=at,
                showings=[],
                blocked_reasons=[],
            )
            if item_id in planted:
                planted_error_id, defect_class, defect_field = planted[item_id]
                entry.update(
                    planted_error_id=planted_error_id,
                    defect_class=defect_class,
                    defect_field=defect_field,
                )
            items[item_id] = entry
        elif item_id in items:
            entry = items[item_id]
            if kind is EventType.SHOWN:
                reviewer = event["reviewer_id"]
                entry["showings"].append(Showing(event["event_id"], reviewer, at))
                if "planted_error_id" in entry:
                    first_seen.setdefault((reviewer, entry["planted_error_id"]), item_id)
            elif kind is EventType.DECIDED and "decision" not in entry:
                entry.update(
                    decision=Decision(event["decision"]),
                    reviewer=event["reviewer_id"],
                    decided_at=at,
                    decided_event=event["event_id"],
                    edited_fields=tuple((data or {}).get("edited_fields", ())),
                )
            elif kind is EventType.EXECUTED:
                entry["executed"] = True
            elif kind is EventType.EXECUTE_FAILED:
                entry["execute_failed"] = True
            elif kind is EventType.BLOCKED:
                reason = data.get("reason", "unknown")
                entry["blocked_reasons"].append(reason)
                if reason != "planted":
                    refusals.append(Refusal(entry["queue"], at, reason))

    records = []
    for entry in items.values():
        entry["showings"] = tuple(entry["showings"])
        entry["blocked_reasons"] = tuple(entry["blocked_reasons"])
        record = ItemRecord(**entry)
        if record.planted and record.decided:
            first = first_seen.get((record.reviewer, record.planted_error_id))
            record = replace(record, first_sight=first == record.item_id)
        records.append(record)
    return Projection(tuple(records), contexts, tuple(refusals), head)
