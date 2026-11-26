"""The planted-error library: import what users write, and have it approved (ADR-003).

Everything here lives in ``planted.db``. The tool never samples or copies live items.

ReviewProof has no sign-in, so it cannot prove who an approver is. It requires the author and
the approvers to be different names. Who can write to ``planted.db`` is controlled by the file's
permissions, and formal sign-off belongs in the customer's quality system.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from reviewproof.canonical import canonical_json, utc_now
from reviewproof.config import Config, QueueConfig
from reviewproof.itemschema import ItemSchema, SchemaError, json_equal
from reviewproof.models import DefectClass
from reviewproof.store.schema import StoreError
from reviewproof.strictload import LoadError, load_file

_REQUIRED = frozenset(
    {
        "planted_error_id",
        "queue",
        "defect_class",
        "defect_field",
        "expected_decision",
        "author",
        "payload",
    }
)
_OPTIONAL = frozenset({"correct_value", "notes"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

_COLUMNS = (
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
    "retired_at",
    "retired_by",
    "retire_reason",
)


class PlantedImportError(ValueError):
    """A planted-error file was refused. The message says what to fix."""


class ApprovalError(ValueError):
    """An approval or retirement was refused."""


class UnknownPlantedError(LookupError):
    """There is no planted error with that ID."""


class Status(str, Enum):
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_SECOND_APPROVAL = "awaiting_second_approval"
    APPROVED = "approved"
    RETIRED = "retired"


@dataclass(frozen=True)
class PlantedError:
    """A stored planted error. Secret: keep it away from reviewers and out of host logs."""

    planted_error_id: str
    queue: str
    defect_class: DefectClass
    defect_field: str
    expected_decision: str  # "reject", or "edit:<defect_field>"
    author: str
    imported_at: str
    payload: Mapping[str, Any] = field(repr=False)
    reserved_target_id: str = field(repr=False)
    has_correct_value: bool = False
    correct_value: Any = field(default=None, repr=False)
    notes: str | None = field(default=None, repr=False)
    approved_by: str | None = None
    approved_at: str | None = None
    second_approved_by: str | None = None
    second_approved_at: str | None = None
    retired_at: str | None = None
    retired_by: str | None = None
    retire_reason: str | None = None

    @property
    def approvers(self) -> frozenset[str]:
        """The different people, other than the author, who approved it."""
        names = {_person(n) for n in (self.approved_by, self.second_approved_by) if n}
        return frozenset(names - {_person(self.author), ""})

    def status(self, gxp: bool) -> Status:
        if self.retired_at is not None:
            return Status.RETIRED
        if len(self.approvers) >= approvals_needed(gxp):
            return Status.APPROVED
        return Status.AWAITING_SECOND_APPROVAL if self.approvers else Status.AWAITING_APPROVAL


def approvals_needed(gxp: bool) -> int:
    return 2 if gxp else 1


class PlantedLibrary:
    """Manage planted errors through a connection from ``open_planted_db``."""

    def __init__(
        self, connection: sqlite3.Connection, config: Config, clock: Callable[[], str] = utc_now
    ) -> None:
        self._db = connection
        self._config = config
        self._clock = clock

    # ------------------------------------------------------------- import

    def import_file(self, path: str | Path) -> PlantedError:
        """Check a YAML or JSON file and store it, not yet approved."""
        try:
            data = load_file(Path(path))
        except LoadError as error:
            raise PlantedImportError(str(error)) from error
        try:
            return self.import_data(data)
        except PlantedImportError as error:
            raise PlantedImportError(f"{path}: {error}") from error

    def import_data(self, data: Any) -> PlantedError:
        values = self._parse(data)
        with self._writing():
            if self._row(values["planted_error_id"]) is not None:
                raise PlantedImportError(
                    f"{values['planted_error_id']} already exists. An imported planted error "
                    "cannot be changed; give the new version a new ID."
                )
            twin = self._db.execute(
                "SELECT planted_error_id FROM planted_errors "
                "WHERE queue_id = ? AND payload = ? AND retired_at IS NULL",
                (values["queue_id"], values["payload"]),
            ).fetchone()
            if twin is not None:
                raise PlantedImportError(f"has the same payload as {twin[0]} in this queue")
            row = {
                **values,
                "reserved_target_id": self._config.reserved_target_prefix + secrets.token_hex(12),
                "imported_at": self._clock(),
            }
            self._db.execute(
                f"INSERT INTO planted_errors ({', '.join(row)}) "
                f"VALUES ({', '.join('?' * len(row))})",
                tuple(row.values()),
            )
        return self.get(values["planted_error_id"])

    def _parse(self, data: Any) -> dict[str, Any]:
        """Validate the user's input and return the columns to store."""
        if not isinstance(data, dict):
            raise PlantedImportError("a planted error must be a mapping of keys to values")
        unknown = sorted(str(key) for key in data.keys() - _REQUIRED - _OPTIONAL)
        if unknown:
            raise PlantedImportError(f"unknown key(s): {', '.join(unknown)}")
        missing = sorted(_REQUIRED - data.keys())
        if missing:
            raise PlantedImportError(f"missing key(s): {', '.join(missing)}")

        planted_error_id = data["planted_error_id"]
        if not isinstance(planted_error_id, str) or not _ID.fullmatch(planted_error_id):
            raise PlantedImportError(
                "planted_error_id must be 1-64 letters, digits, dots, hyphens or underscores"
            )
        queue = self._config.queues.get(data["queue"]) if _is_text(data["queue"]) else None
        if queue is None:
            raise PlantedImportError(
                f"unknown queue. Configured queues: {', '.join(sorted(self._config.queues))}"
            )
        try:
            defect_class = DefectClass(data["defect_class"])
        except ValueError:
            allowed = ", ".join(member.value for member in DefectClass)
            raise PlantedImportError(f"defect_class must be one of: {allowed}") from None

        defect_field = data["defect_field"]
        if not _is_text(defect_field):
            raise PlantedImportError("defect_field must name a property of the item")
        if data["expected_decision"] not in ("reject", f"edit:{defect_field}"):
            raise PlantedImportError(
                f"expected_decision must be 'reject' or 'edit:{defect_field}' "
                "(an edit must name the defect_field)"
            )
        author = data["author"]
        if not _is_text(author) or not _person(author):
            raise PlantedImportError("author must be a non-empty name or ID")
        notes = data.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise PlantedImportError("notes must be text")

        payload = data["payload"]
        if not isinstance(payload, dict):
            raise PlantedImportError("payload must be the whole item, as a mapping")
        payload_text = _as_json(payload, "payload")
        has_correct_value = "correct_value" in data
        correct_text = (
            _as_json(data["correct_value"], "correct_value") if has_correct_value else None
        )

        problems = self._content_problems(
            queue, defect_field, payload, has_correct_value, data.get("correct_value")
        )
        if problems:
            raise PlantedImportError("; ".join(problems))

        return {
            "planted_error_id": planted_error_id,
            "queue_id": queue.name,
            "defect_class": defect_class.value,
            "defect_field": defect_field,
            "expected_decision": data["expected_decision"],
            "payload": payload_text,
            "correct_value": correct_text,
            "notes": notes,
            "author": author.strip(),
        }

    def _content_problems(
        self,
        queue: QueueConfig,
        defect_field: str,
        payload: Mapping[str, Any],
        has_correct_value: bool,
        correct_value: Any,
    ) -> list[str]:
        """What would stop this content being used in the queue as it is configured now.

        A planted item must pass the same schema as a real one, or reviewers could spot it.
        """
        try:
            schema = ItemSchema.load(queue.item_schema)
        except SchemaError as error:
            return [f"the item schema for queue {queue.name!r} cannot be used: {error}"]

        problems = schema.errors(dict(payload))
        prefix = self._config.reserved_target_prefix.casefold()
        if any(prefix in text.casefold() for text in _texts(payload)):
            problems.append(
                "payload contains the reserved target prefix. Target IDs never go in an item."
            )
        if defect_field not in schema.property_names:
            problems.append(
                f"defect_field {defect_field!r} is not a property in the item schema. It has: "
                f"{', '.join(sorted(schema.property_names))}"
            )
        elif has_correct_value:
            problems += schema.property_errors(defect_field, correct_value, "correct_value")
            if defect_field in payload and json_equal(payload[defect_field], correct_value):
                problems.append(
                    f"payload.{defect_field} equals correct_value, so there is no defect"
                )
        return problems

    # ------------------------------------------------------------- approval

    def approve(self, planted_error_id: str, approver: str) -> PlantedError:
        """Record one approval. A GxP queue needs two, from two people other than the author."""
        _require_person(approver, "approver")
        with self._writing():
            planted = self.get(planted_error_id)
            queue = self._config.queues.get(planted.queue)
            if queue is None:
                raise ApprovalError(f"queue {planted.queue!r} is no longer configured")
            if planted.retired_at is not None:
                raise ApprovalError(f"{planted_error_id} is retired")
            if _person(approver) == _person(planted.author):
                raise ApprovalError("authors cannot approve their own planted errors")
            if planted.status(queue.gxp) is Status.APPROVED:
                raise ApprovalError(f"{planted_error_id} already has the approvals it needs")
            if _person(approver) in planted.approvers:
                raise ApprovalError(
                    "this person has already approved it. The second approval must come from "
                    "someone else."
                )
            problems = self.problems(planted)
            if problems:
                raise ApprovalError(f"cannot be approved: {'; '.join(problems)}")
            if planted.approved_by is None:
                column = "approved"
            elif planted.second_approved_by is None:
                column = "second_approved"
            else:
                raise ApprovalError("both approvals are used; import it again under a new ID")
            self._db.execute(
                f"UPDATE planted_errors SET {column}_by = ?, {column}_at = ? "
                "WHERE planted_error_id = ?",
                (approver.strip(), self._clock(), planted_error_id),
            )
        return self.get(planted_error_id)

    def retire(
        self, planted_error_id: str, retired_by: str, reason: str | None = None
    ) -> PlantedError:
        """Stop using a planted error, for example because reviewers spotted it. Permanent."""
        _require_person(retired_by, "retired_by")
        with self._writing():
            if self.get(planted_error_id).retired_at is not None:
                raise ApprovalError(f"{planted_error_id} is already retired")
            self._db.execute(
                "UPDATE planted_errors SET retired_at = ?, retired_by = ?, retire_reason = ? "
                "WHERE planted_error_id = ?",
                (self._clock(), retired_by.strip(), reason, planted_error_id),
            )
        return self.get(planted_error_id)

    # ------------------------------------------------------------- reading

    def get(self, planted_error_id: str) -> PlantedError:
        row = self._row(planted_error_id)
        if row is None:
            raise UnknownPlantedError(f"no planted error {planted_error_id!r}")
        return _from_row(row)

    def all(self, queue: str | None = None) -> list[PlantedError]:
        where, arguments = ("WHERE queue_id = ?", (queue,)) if queue else ("", ())
        return [_from_row(row) for row in self._select(f"{where} ORDER BY rowid", arguments)]

    def status(self, planted: PlantedError) -> Status:
        queue = self._config.queues.get(planted.queue)
        # A queue that is no longer configured gets the stricter rule.
        return planted.status(gxp=True if queue is None else queue.gxp)

    def problems(self, planted: PlantedError) -> list[str]:
        """Why a stored planted error no longer suits its queue, if it does not."""
        queue = self._config.queues.get(planted.queue)
        if queue is None:
            return [f"queue {planted.queue!r} is no longer configured"]
        return self._content_problems(
            queue,
            planted.defect_field,
            planted.payload,
            planted.has_correct_value,
            planted.correct_value,
        )

    def available(self, queue: str) -> list[PlantedError]:
        """The only planted errors that may be injected into ``queue``.

        Approved under the queue's current rules, not retired, and still matching its schema.
        The approval rules are checked here as well as by the database.
        """
        return [planted for planted in self._approved(queue) if not self.problems(planted)]

    def stale(self, queue: str) -> dict[str, list[str]]:
        """Approved planted errors that are held back, for example after a schema change."""
        found = {p.planted_error_id: self.problems(p) for p in self._approved(queue)}
        return {planted_error_id: why for planted_error_id, why in found.items() if why}

    def _approved(self, queue: str) -> list[PlantedError]:
        if queue not in self._config.queues:
            raise UnknownPlantedError(f"queue {queue!r} is not configured")
        return [p for p in self.all(queue) if self.status(p) is Status.APPROVED]

    # ------------------------------------------------------------- storage

    def _select(self, tail: str, arguments: tuple) -> list[sqlite3.Row]:
        try:
            return self._db.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM planted_errors {tail}", arguments
            ).fetchall()
        except sqlite3.Error as error:
            raise StoreError(f"cannot read the planted-error library: {error}") from error

    def _row(self, planted_error_id: str) -> sqlite3.Row | None:
        rows = self._select("WHERE planted_error_id = ?", (planted_error_id,))
        return rows[0] if rows else None

    @contextmanager
    def _writing(self) -> Iterator[None]:
        """One write transaction, so that a check and the change it allows are atomic."""
        if self._db.in_transaction:
            # Joining it would be unsafe, and rolling back would undo the caller's work.
            raise StoreError("the planted-error connection is already in a transaction")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            yield
            self._db.execute("COMMIT")
        except sqlite3.Error as error:
            raise StoreError(f"cannot write the planted-error library: {error}") from error
        finally:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")


def _from_row(row: sqlite3.Row) -> PlantedError:
    values = dict(zip(_COLUMNS, row, strict=True))
    try:
        values["payload"] = json.loads(values["payload"])
        correct = values.pop("correct_value")
        values["has_correct_value"] = correct is not None
        values["correct_value"] = None if correct is None else json.loads(correct)
        values["defect_class"] = DefectClass(values["defect_class"])
    except (ValueError, TypeError) as error:
        raise StoreError(f"planted error {values['planted_error_id']!r} is damaged") from error
    values["queue"] = values.pop("queue_id")
    return PlantedError(**values)


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _person(name: str) -> str:
    """One form per person, so that ``QA-1`` and `` qa-1 `` are not counted as two people."""
    normal = unicodedata.normalize("NFKC", name)
    visible = "".join(c for c in normal if unicodedata.category(c) != "Cf")
    return " ".join(visible.split()).casefold()


def _require_person(name: Any, what: str) -> None:
    if not isinstance(name, str) or not _person(name):
        raise ApprovalError(f"{what} must be a non-empty name or ID")


def _as_json(value: Any, where: str) -> str:
    try:
        return canonical_json(value).decode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise PlantedImportError(
            f"{where} may hold only JSON values. In YAML, put dates and times in quotes. ({error})"
        ) from error


def _texts(value: Any) -> Iterator[str]:
    """Every string in a JSON value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, element in value.items():
            yield key
            yield from _texts(element)
    elif isinstance(value, list):
        for element in value:
            yield from _texts(element)
