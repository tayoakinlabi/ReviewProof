"""The hash-chained event log (ADR-002).

``hash = SHA-256(prev_hash + canonical_json(record))``, where the record is every stored column
except ``hash``. Changing what goes into the record breaks every existing log, so a change needs
a new ``FORMAT_VERSION`` and ``verify`` must keep reading the old ones.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from reviewproof.canonical import canonical_json, sha256_hex, utc_now
from reviewproof.capture import context_digest, trace_digest
from reviewproof.models import Decision, EventType
from reviewproof.store.qualification_check import check_qualification
from reviewproof.store.registry import (
    CONTEXT_COLUMNS,
    TRACE_COLUMNS,
    MissingEntryError,
    RegistryEntry,
    commitment_of,
    read_entry,
)
from reviewproof.store.schema import GENESIS_HASH, StoreError

FORMAT_VERSION = 1

_RECORD_COLUMNS = (
    "event_id",
    "format_version",
    "type",
    "ts_utc",
    "system_id",
    "queue_id",
    "item_id",
    "reviewer_id",
    "decision",
    "data",
    "prev_hash",
)
_COLUMNS = (*_RECORD_COLUMNS, "hash")
_OPTIONAL_TEXT = ("queue_id", "item_id", "reviewer_id", "decision")
_REQUIRED_TEXT = ("type", "ts_utc", "system_id", "data", "prev_hash", "hash")


@dataclass(frozen=True)
class Event:
    event_id: int
    format_version: int
    type: EventType
    ts_utc: str
    system_id: str
    queue_id: str | None
    item_id: str | None
    reviewer_id: str | None
    decision: Decision | None
    data: Mapping[str, Any] = field(repr=False)
    prev_hash: str
    hash: str


@dataclass(frozen=True)
class Head:
    """The end of the chain. Exported to storage that this tool cannot write to."""

    event_count: int
    head_hash: str


@dataclass(frozen=True)
class VerifyResult:
    #: The events that verified. If the chain is broken, those before the break.
    head: Head
    chain_error: str | None = None
    #: The ``event_id`` of the first broken link.
    broken_at: int | None = None
    registry_checked: bool = False
    registry_errors: tuple[str, ...] = ()
    anchor_error: str | None = None

    @property
    def ok(self) -> bool:
        return not (self.chain_error or self.registry_errors or self.anchor_error)


class EventLog:
    """Append to the log through a connection from ``open_oversight_db``.

    A connection belongs to one thread. Give each thread its own.
    """

    def __init__(self, connection: sqlite3.Connection, clock: Callable[[], str] = utc_now) -> None:
        self._db = connection
        self._clock = clock
        self._in_transaction = False

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Hold the write lock, so that a check and the events that rely on it are atomic.

        Everything done on the connection inside the block commits together or not at all.
        Blocks may be nested; the outermost one commits.
        """
        if self._in_transaction:
            yield
            return
        if self._db.in_transaction:
            # A transaction started elsewhere may not hold the write lock.
            raise StoreError("use EventLog.transaction() to group writes with events")
        try:
            self._db.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as error:
            raise StoreError(f"cannot lock the event log: {error}") from error
        self._in_transaction = True
        try:
            yield
            self._db.execute("COMMIT")
        except sqlite3.Error as error:
            raise StoreError(f"cannot write to the event log: {error}") from error
        finally:
            self._in_transaction = False
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")

    def append(
        self,
        event_type: EventType,
        *,
        system_id: str,
        queue_id: str | None = None,
        item_id: str | None = None,
        reviewer_id: str | None = None,
        decision: Decision | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> Event:
        record: dict[str, Any] = {
            "format_version": FORMAT_VERSION,
            "type": EventType(event_type).value,
            "system_id": system_id,
            "queue_id": queue_id,
            "item_id": item_id,
            "reviewer_id": reviewer_id,
            "decision": None if decision is None else Decision(decision).value,
        }
        if not isinstance(system_id, str) or not system_id:
            raise ValueError("system_id must be a non-empty string")
        for name in ("queue_id", "item_id", "reviewer_id"):
            if record[name] is not None and not isinstance(record[name], str):
                raise TypeError(f"{name} must be a string or None")
        data = {} if data is None else dict(data)
        _reject_floats(data, "data")
        data_text = canonical_json(data).decode("utf-8")

        with self.transaction():
            head = self.head()
            record["event_id"] = head.event_count + 1
            record["prev_hash"] = head.head_hash
            record["ts_utc"] = self._clock()
            # Parsed back from the stored text, so the caller cannot change it afterwards.
            record["data"] = json.loads(data_text)
            digest = compute_hash(record)
            row = {**record, "data": data_text, "hash": digest}
            self._db.execute(
                f"INSERT INTO events ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                tuple(row[column] for column in _COLUMNS),
            )
        return _event({**record, "hash": digest})

    def head(self) -> Head:
        """The last event's number and hash. This reads the head; it does not verify the chain."""
        try:
            row = self._db.execute(
                "SELECT event_id, hash FROM events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
        except sqlite3.Error as error:
            raise StoreError(f"cannot read the event log: {error}") from error
        return Head(0, GENESIS_HASH) if row is None else Head(row["event_id"], row["hash"])


def compute_hash(record: Mapping[str, Any]) -> str:
    """Hash one event. ``record`` holds every column but ``hash``, with ``data`` parsed."""
    body = {column: record[column] for column in _RECORD_COLUMNS}
    return sha256_hex(record["prev_hash"].encode("ascii") + canonical_json(body))


class IntegrityError(StoreError):
    """An event that something is about to rely on does not hold up."""


def item_events(connection: sqlite3.Connection, item_id: str) -> list[dict[str, Any]]:
    """An item's events in order, each checked before it is relied on: its hash, and its links
    to the events on both sides. ``data`` is returned parsed.

    This is a local check, cheap enough to run before every execution. Someone who rewrites the
    whole log from an event onwards passes it; ``verify`` with an exported head catches that.
    """
    try:
        rows = connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM events WHERE item_id = ? ORDER BY event_id",
            (item_id,),
        ).fetchall()
        checked = []
        for row in rows:
            record = dict(zip(_COLUMNS, row, strict=True))
            _check_in_place(connection, record)
            checked.append({**record, "data": json.loads(record["data"])})
    except sqlite3.Error as error:
        raise StoreError(f"cannot read the event log: {error}") from error
    return checked


def session_events(
    connection: sqlite3.Connection, queue: str, session_id: str
) -> list[dict[str, Any]]:
    """A qualification session's events in order, each checked like ``item_events``."""
    try:
        rows = connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM events WHERE queue_id = ? "
            "AND type LIKE 'qualification_%' AND instr(data, ?) > 0 ORDER BY event_id",
            (queue, json.dumps(session_id)),
        ).fetchall()
        checked = []
        for row in rows:
            record = dict(zip(_COLUMNS, row, strict=True))
            _check_in_place(connection, record)
            data = json.loads(record["data"])
            if data.get("session_id") == session_id:
                checked.append({**record, "data": data})
    except sqlite3.Error as error:
        raise StoreError(f"cannot read the event log: {error}") from error
    return checked


def _check_in_place(connection: sqlite3.Connection, record: Mapping[str, Any]) -> None:
    number = record["event_id"]
    previous = GENESIS_HASH
    if number > 1:
        before = connection.execute(
            "SELECT hash FROM events WHERE event_id = ?", (number - 1,)
        ).fetchone()
        previous = None if before is None else before[0]
    problem = _check_link(record, number, previous)
    if problem:
        raise IntegrityError(problem)
    after = connection.execute(
        "SELECT event_id, prev_hash FROM events WHERE event_id > ? ORDER BY event_id LIMIT 1",
        (number,),
    ).fetchone()
    if after is not None and (after[0] != number + 1 or after[1] != record["hash"]):
        raise IntegrityError(f"the event after event {number} does not follow it")


def verify(
    oversight: sqlite3.Connection,
    planted: sqlite3.Connection | None = None,
    expected: Head | None = None,
) -> VerifyResult:
    """Recompute the chain, and optionally check the registry and an exported head.

    Never trusts the database's own constraints or triggers: whoever tampered may have removed
    them. Writes nothing.
    """
    count, previous = 0, GENESIS_HASH
    chain_error = broken_at = anchor_error = None
    submitted: dict[str, tuple[int, Mapping[str, Any]]] = {}
    shown: list[tuple[int, str, str, Mapping[str, Any]]] = []
    duplicates: list[str] = []
    qualification: list[dict[str, Any]] = []
    hash_at_expected = GENESIS_HASH if expected and expected.event_count == 0 else None

    # Text that is not UTF-8 is read as bytes, so the type check reports it at its own event.
    # Letting the read raise would name the wrong event: some Python versions read a row ahead.
    text_factory = oversight.text_factory
    oversight.text_factory = _decode_if_possible
    try:
        cursor = oversight.execute(f"SELECT {', '.join(_COLUMNS)} FROM events ORDER BY event_id")
        for row in cursor:
            record = dict(zip(_COLUMNS, row, strict=True))
            chain_error = _check_link(record, count + 1, previous)
            if chain_error:
                broken_at = count + 1
                break
            count, previous = count + 1, record["hash"]
            if expected and count == expected.event_count:
                hash_at_expected = previous
            if record["type"] == EventType.SUBMITTED.value:
                item_id = record["item_id"]
                if item_id in submitted:
                    duplicates.append(f"event {count}: item {item_id} was submitted twice")
                else:
                    submitted[item_id] = (count, json.loads(record["data"]))
            elif record["type"].startswith("qualification_"):
                qualification.append({**record, "data": json.loads(record["data"])})
            elif record["type"] == EventType.SHOWN.value:
                data = json.loads(record["data"])
                shown.append((count, record["item_id"], record["reviewer_id"], data))
    except sqlite3.Error as error:
        broken_at = count + 1
        chain_error = f"the log cannot be read after {count} verified events: {error}"
    finally:
        oversight.text_factory = text_factory

    if expected is not None:
        if hash_at_expected is None:
            anchor_error = (
                f"the exported head is event {expected.event_count}, but only {count} events "
                "verify: events are missing or changed"
            )
        elif hash_at_expected != expected.head_hash:
            anchor_error = (
                f"event {expected.event_count} does not match the exported head: "
                "the log was rebuilt or rewritten"
            )

    registry_errors = list(duplicates)
    if planted is not None:
        registry_errors += _check_registry(planted, submitted, shown)
        registry_errors += check_qualification(planted, qualification)

    return VerifyResult(
        head=Head(count, previous),
        chain_error=chain_error,
        broken_at=broken_at,
        registry_checked=planted is not None,
        registry_errors=tuple(registry_errors),
        anchor_error=anchor_error,
    )


def _decode_if_possible(raw: bytes) -> str | bytes:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw


def _check_link(record: Mapping[str, Any], expected_id: int, previous: str) -> str | None:
    """Return what is wrong with this link, or None. Must not raise on tampered values."""
    name = f"event {expected_id}"
    if record["event_id"] != expected_id:
        return f"{name} is missing: the next event found is {record['event_id']}"
    if type(record["format_version"]) is not int or record["format_version"] != FORMAT_VERSION:
        return f"{name} has an unsupported format version: {record['format_version']!r}"
    if any(not isinstance(record[column], str) for column in _REQUIRED_TEXT) or any(
        record[column] is not None and not isinstance(record[column], str)
        for column in _OPTIONAL_TEXT
    ):
        return f"{name} has a value of the wrong type"
    if record["prev_hash"] != previous:
        return (
            f"{name} does not follow the event before it: an event was changed, removed or inserted"
        )
    try:
        data = json.loads(record["data"])
        _reject_floats(data, "data")
        # The stored text itself must be canonical, so that no edit to it goes unnoticed.
        if not isinstance(data, dict) or canonical_json(data).decode("utf-8") != record["data"]:
            return f"{name} has data that is not in canonical form"
        digest = compute_hash({**record, "data": data})
    except (ValueError, TypeError, RecursionError) as error:
        return f"{name} cannot be hashed: {error}"
    if digest != record["hash"]:
        return f"{name} does not match its hash: it was changed after it was written"
    return None


def _check_registry(
    planted: sqlite3.Connection,
    submitted: Mapping[str, tuple[int, Mapping[str, Any]]],
    shown: list[tuple[int, str, str, Mapping[str, Any]]],
) -> list[str]:
    """Compare what the restricted database holds for each item with what the log committed to:
    the registry row and any planted error behind it, the trace, and each review context.

    Rows without an event are harmless: a crash can leave one (ADR-001). Messages must never
    say whether an item is real or planted, so every mismatch reads the same.
    """
    errors = []
    entries: dict[str, RegistryEntry] = {}
    for item_id, (event_id, data) in submitted.items():
        where = f"event {event_id}, item {item_id}"
        try:
            entry = read_entry(planted, item_id)
            if data.get("commitment") != commitment_of(planted, entry):
                errors.append(f"{where}: the registry row does not match the log")
                continue
            entries[item_id] = entry
            if "trace_hash" in data and data["trace_hash"] != _stored_trace_digest(planted, entry):
                errors.append(f"{where}: the trace records do not match the log")
        except (StoreError, sqlite3.Error, ValueError, TypeError) as error:
            errors.append(f"{where}: {_problem(error)}")
    for event_id, item_id, reviewer, data in shown:
        entry = entries.get(item_id)
        if entry is None or "context_hash" not in data:
            continue
        where = f"event {event_id}, item {item_id}"
        try:
            if not _context_matches(planted, entry, reviewer, data["context_hash"]):
                errors.append(f"{where}: the review context does not match the log")
        except (StoreError, sqlite3.Error, ValueError, TypeError) as error:
            errors.append(f"{where}: {_problem(error)}")
    return errors


def _problem(error: Exception) -> str:
    """Only these three messages. A more detailed one could differ between real and planted."""
    if isinstance(error, sqlite3.Error) or isinstance(error.__cause__, sqlite3.Error):
        return "the registry cannot be read"
    if isinstance(error, MissingEntryError):
        return "no registry row"
    return "the registry row does not match the log"


def _stored_trace_digest(planted: sqlite3.Connection, entry: RegistryEntry) -> str:
    rows = planted.execute(
        f"SELECT {', '.join(TRACE_COLUMNS)} FROM traces WHERE item_id = ?", (entry.item_id,)
    ).fetchall()
    records = [dict(zip(TRACE_COLUMNS, row, strict=True)) for row in rows]
    return trace_digest(records, entry.payload_salt)


def _context_matches(
    planted: sqlite3.Connection, entry: RegistryEntry, reviewer: str, expected: str
) -> bool:
    if expected == context_digest(None, entry.payload_salt):
        return True  # the host gave no context for this showing
    rows = planted.execute(
        f"SELECT {', '.join(CONTEXT_COLUMNS)} FROM review_context "
        "WHERE item_id = ? AND reviewer_id = ?",
        (entry.item_id, reviewer),
    ).fetchall()
    for row in rows:
        manifest = {
            name.removeprefix("shown_"): json.loads(value)
            for name, value in zip(CONTEXT_COLUMNS, row, strict=True)
        }
        if context_digest(manifest, entry.payload_salt) == expected:
            return True
    return False


def _reject_floats(value: Any, where: str) -> None:
    if isinstance(value, float):
        raise TypeError(f"{where}: event records hold no decimals; use whole milliseconds")
    if isinstance(value, dict):
        for key, element in value.items():
            _reject_floats(element, f"{where}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, element in enumerate(value):
            _reject_floats(element, f"{where}[{index}]")


def _event(record: Mapping[str, Any]) -> Event:
    values = dict(record)
    values["type"] = EventType(values["type"])
    if values["decision"] is not None:
        values["decision"] = Decision(values["decision"])
    return Event(**values)
