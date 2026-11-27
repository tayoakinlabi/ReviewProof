"""The execution guard: the last check before anything is acted on (ADR-003).

The executor is called only when every check passes. **Any** failure, including one nobody
foresaw, means that nothing runs.

1. The item has one decision, ``approve`` or ``edit``, and no earlier request to execute.
2. The log entries relied on are intact and linked to their neighbours.
3. The registry says ``real``, and still matches the commitment made at submission.
4. The payload is the one that was approved.
5. Without relying on the registry's ``real`` being right: a target ID about to be acted on is
   not one reserved for planted errors.

``execute_requested`` is logged before the executor is called, so each item runs at most once.
Every check that reads a database comes before it, so an outage never uses up that one attempt.
Just before the call, a last test of the target ID that needs no database repeats part of
check 5. It would still stop a planted item if the real-or-planted test were ever broken.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reviewproof.config import Config
from reviewproof.errors import (
    GuardError,
    OversightError,
    TransitionError,
    UnknownTokenError,
    UnrecordedExecutionError,
)
from reviewproof.feedback import score
from reviewproof.models import Decision, EventType, ItemKind
from reviewproof.planted import PlantedLibrary
from reviewproof.records import ItemRef, Records, plain_json
from reviewproof.store.commitments import payload_hash
from reviewproof.store.eventlog import item_events
from reviewproof.store.registry import commitment_of, read_entry
from reviewproof.store.schema import StoreError

_log = logging.getLogger("reviewproof")

Executor = Callable[[str, dict[str, Any]], Any]


@dataclass(frozen=True)
class ExecutionResult:
    #: False when nothing was executed because the item must never be. Show reviewers nothing
    #: different when this happens.
    executed: bool
    #: What the executor returned.
    value: Any = field(default=None, repr=False)


class _Refused(Exception):
    """A check said no. ``reason`` goes into the log."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class _Authorised:
    item: ItemRef
    kind: ItemKind
    target_id: str = field(repr=False)
    reviewer: str = ""


class Guard:
    def __init__(
        self,
        records: Records,
        planted: sqlite3.Connection,
        library: PlantedLibrary,
        config: Config,
        files: Sequence[Path],
    ) -> None:
        self._records = records
        self._planted = planted
        self._library = library
        self._config = config
        self._files = files

    def execute(
        self, token: str, payload: Mapping[str, Any], executor: Executor
    ) -> ExecutionResult:
        if not callable(executor):
            raise OversightError("executor must be a function of (target_id, payload)")
        approved = plain_json(payload, "payload")
        authorised = self._authorise(token, approved)
        # A positive test: anything but "real" stops here.
        if authorised.kind is not ItemKind.REAL:
            return ExecutionResult(executed=False)
        self._last_check(token, authorised.target_id)
        return self._run(authorised, approved, executor)

    def settle(self, token: str, payload: Mapping[str, Any]) -> None:
        """For an approved or edited item whose executor is somewhere else (ADR-005).

        Checks the payload as ``execute()`` would, for either kind of item. For anything but a
        real item it then records what ``execute()`` records. It has nothing to call, and
        repeating it changes nothing. A real item is left for its one execution.
        """
        self._authorise(token, plain_json(payload, "payload"), settling=True)

    # ------------------------------------------------------------- checks

    def _authorise(
        self, token: str, payload: dict[str, Any], settling: bool = False
    ) -> _Authorised:
        """Run the checks and record the request, atomically."""
        try:
            self._require_files()
            with self._records.log.transaction():
                return self._check_and_record(token, payload, settling)
        except (UnknownTokenError, TransitionError):
            raise
        except _Refused as refusal:
            self._record_blocked(token, refusal.reason)
            raise GuardError(f"{refusal} Nothing was executed.") from None
        except Exception as error:
            raise self._failed(token, error) from None

    def _check_and_record(self, token: str, payload: dict[str, Any], settling: bool) -> _Authorised:
        item = self._records.find(token)
        events: dict[str, list[dict[str, Any]]] = {}
        for event in item_events(self._records.db, item.item_id):
            events.setdefault(event["type"], []).append(event)

        requested = EventType.EXECUTE_REQUESTED.value in events
        if requested and not settling:
            raise TransitionError("execution has already been requested for this item")
        decisions = events.get(EventType.DECIDED.value, [])
        if not decisions:
            raise TransitionError("this item has no decision")
        if decisions[0]["decision"] == Decision.REJECT.value:
            raise TransitionError("a rejected item cannot be executed")
        submissions = events.get(EventType.SUBMITTED.value, [])
        if len(decisions) != 1 or len(submissions) != 1:
            raise _Refused("check_failed", "The log's account of this item is inconsistent.")
        decision, submitted = decisions[0], submissions[0]
        if decision["decision"] not in (Decision.APPROVE.value, Decision.EDIT.value):
            raise _Refused("check_failed", "The decision does not allow execution.")

        entry = read_entry(self._planted, item.item_id)
        if submitted["data"]["commitment"] != commitment_of(self._planted, entry):
            raise _Refused("check_failed", "The registry does not match the log.")

        if decision["decision"] == Decision.APPROVE.value:
            expected = submitted["data"]["payload_hash"]
        else:
            expected = decision["data"]["final_payload_hash"]
        if payload_hash(payload, entry.payload_salt) != expected:
            raise _Refused("payload_changed", "The payload is not the one that was approved.")

        if entry.kind is ItemKind.REAL and self._is_reserved(entry.target_id):
            _log.error("the guard stopped a reserved target ID in queue %r", self._records.queue)
            raise _Refused("reserved_target", "The target ID is reserved.")

        reviewer = decision["reviewer_id"]
        # The context the item was submitted under, taken from the log.
        item = ItemRef(item.item_id, submitted["system_id"])
        authorised = _Authorised(item, entry.kind, entry.target_id, reviewer)
        # Settling records nothing for a real item: its one execution is still to come.
        if settling and (requested or entry.kind is ItemKind.REAL):
            return authorised
        self._records.append(EventType.EXECUTE_REQUESTED, item, reviewer)
        if entry.kind is not ItemKind.REAL:
            planted = self._library.get(entry.planted_error_id)
            edited = (decision["data"] or {}).get("edited_fields", [])
            outcome = score(decision["decision"], edited, planted.defect_field)
            self._records.append(EventType.BLOCKED, item, reviewer, data={"reason": "planted"})
            self._records.append(
                EventType.WITHDRAWN, item, reviewer, data={"outcome": outcome.value}
            )
        return authorised

    def _is_reserved(self, target_id: str) -> bool:
        """Is this a planted error's target ID? Does not depend on the item's recorded kind."""
        if self._has_reserved_prefix(target_id):
            return True
        # Still works if the configured prefix has changed since the planted error was written.
        return (
            self._planted.execute(
                "SELECT 1 FROM planted_errors WHERE reserved_target_id = ?", (target_id,)
            ).fetchone()
            is not None
        )

    def _has_reserved_prefix(self, target_id: str) -> bool:
        prefix = self._config.reserved_target_prefix.casefold()
        return target_id.strip().casefold().startswith(prefix)

    def _last_check(self, token: str, target_id: str) -> None:
        """Immediately before the executor, with nothing that an outage could break."""
        if self._has_reserved_prefix(target_id):
            _log.error("the guard stopped a reserved target ID in queue %r", self._records.queue)
            self._record_blocked(token, "reserved_target")
            raise GuardError("The target ID is reserved. Nothing was executed.")

    def _require_files(self) -> None:
        # An open connection can outlive a deleted file on some systems.
        if not all(path.is_file() for path in self._files):
            raise StoreError("a database file is missing")

    # ------------------------------------------------------------- execution

    def _run(
        self, authorised: _Authorised, payload: dict[str, Any], executor: Executor
    ) -> ExecutionResult:
        try:
            value = executor(authorised.target_id, payload)
        except BaseException as error:
            self._best_effort(
                EventType.EXECUTE_FAILED, authorised, {"error_type": type(error).__name__}
            )
            raise
        try:
            with self._records.log.transaction():
                self._records.append(EventType.EXECUTED, authorised.item, authorised.reviewer)
        except Exception as error:
            raise UnrecordedExecutionError(
                "the executor ran, but this could not be logged. Do not run the item again; "
                "check the target system and the log."
            ) from error
        return ExecutionResult(executed=True, value=value)

    # ------------------------------------------------------------- recording

    def _failed(self, token: str, error: Exception) -> GuardError:
        # Only the type: a message could name a planted error in the host's logs.
        name = type(error).__name__
        _log.error("a guard check failed in queue %r (%s)", self._records.queue, name)
        self._record_blocked(token, "check_failed")
        return GuardError(
            f"A safety check could not be completed ({name}). Nothing was executed. Run rp verify."
        )

    def _record_blocked(self, token: str, reason: str) -> None:
        try:
            with self._records.log.transaction():
                item = self._records.find(token)
                self._records.append(EventType.BLOCKED, item, None, data={"reason": reason})
        except Exception as error:
            _log.error("a blocked execution could not be logged (%s)", type(error).__name__)

    def _best_effort(
        self, event_type: EventType, authorised: _Authorised, data: Mapping[str, Any]
    ) -> None:
        try:
            with self._records.log.transaction():
                self._records.append(event_type, authorised.item, authorised.reviewer, data=data)
        except Exception as error:
            _log.error("an execution result could not be logged (%s)", type(error).__name__)
