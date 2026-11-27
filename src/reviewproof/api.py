"""The API a host application calls around its own approval step (ADR-003).

Nothing returned or raised here says whether an item is planted. Real and planted items take the
same path, leave rows and events of the same shape, and are refused for the same reasons.
"""

from __future__ import annotations

import json
import logging
import random
import secrets
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from reviewproof import adapters, feedback
from reviewproof.canonical import utc_now
from reviewproof.capture import context_digest, review_manifest, trace_digest, trace_records
from reviewproof.config import Config, load_config
from reviewproof.errors import (
    GuardError,
    OversightError,
    TransitionError,
    UnknownTokenError,
)
from reviewproof.gate import Gate, GateStatus
from reviewproof.guard import ExecutionResult, Executor, Guard
from reviewproof.itemschema import ItemSchema, SchemaError
from reviewproof.keyed import KeyedSubmissions
from reviewproof.models import (
    CallStatus,
    Decision,
    EventType,
    FeedbackTiming,
    Item,
    ItemKind,
    OversightContext,
    QualificationStatus,
    Ticket,
    derive_token,
    new_item_id,
    new_token,
)
from reviewproof.planted import PlantedLibrary
from reviewproof.qualification import Qualification
from reviewproof.records import (
    ItemRef,
    Records,
    plain_json,
    require_key,
    require_reviewer,
    token_hash,
)
from reviewproof.store.commitments import field_hashes, new_salt, payload_hash, planted_commitment
from reviewproof.store.eventlog import EventLog, item_events
from reviewproof.store.registry import (
    CONTEXT_COLUMNS,
    add_key,
    add_real_item,
    key_of,
    read_entry,
)
from reviewproof.store.schema import (
    StoreError,
    create_oversight_db,
    create_planted_db,
    open_oversight_db,
    open_planted_db,
)

_log = logging.getLogger("reviewproof")

__all__ = [
    "Oversight",
    "OversightError",
    "TransitionError",
    "UnknownTokenError",
    "initialise",
]


def initialise(db: str | Path, planted_db: str | Path, *, upgrade: bool = False) -> None:
    """Create the two databases, once, when setting up a deployment.

    Databases made by an earlier release are refused unless ``upgrade`` is true. Back up both
    first: an upgrade cannot be undone. Do not pass ``upgrade=True`` from code that runs every
    time your application starts.

    If exactly one of them exists, this refuses: replacing a lost file with an empty one would
    hide the loss. Restore it from a backup, as a pair with the other.
    """
    db, planted_db = Path(db), Path(planted_db)
    if db.resolve() == planted_db.resolve():
        raise OversightError("the two databases must be different files")
    if db.exists() != planted_db.exists():
        missing = planted_db if db.exists() else db
        raise OversightError(
            f"only one of the two databases exists; {missing} is missing. Restore both from "
            "the same backup."
        )
    create_oversight_db(db, upgrade=upgrade).close()
    create_planted_db(planted_db, upgrade=upgrade).close()


@dataclass(frozen=True)
class _Submission:
    """One item ready to be written, real or planted."""

    item_id: str
    token: str
    payload: dict[str, Any]
    payload_salt: bytes
    commitment: str
    trace_hash: str


class Oversight:
    """One queue's gate and log.

    Holds database connections, which belong to one thread: give each thread or process its own
    instance. Close it, or use it as a context manager.
    """

    def __init__(
        self,
        config: Config | str | Path,
        queue: str,
        db: str | Path = "oversight.db",
        planted_db: str | Path = "planted.db",
        system: str | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self._config = config if isinstance(config, Config) else load_config(config)
        settings = self._config.queues.get(queue)
        if settings is None:
            raise OversightError(f"queue {queue!r} is not configured")
        if system is not None and system != settings.system:
            raise OversightError(f"queue {queue!r} belongs to system {settings.system!r}")
        self._queue = settings
        self._clock = clock
        self._rng = rng if rng is not None else secrets.SystemRandom()

        # Absolute, so that a later change of folder does not matter.
        self._files = (Path(db).resolve(), Path(planted_db).resolve())
        self._context = self._config.systems[settings.system]

        # Opened, never created: see initialise().
        self._db = open_oversight_db(db)
        try:
            self._planted = open_planted_db(planted_db)
        except BaseException:
            self._db.close()
            raise
        try:
            self._log = EventLog(self._db, clock)
            self._records = Records(self._db, self._log, settings.name)
            self._gate = Gate(self._planted, self._db, self._config, self._rng, clock)
            self._system_id, _ = self._record_context(self._context)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self._db.close()
        self._planted.close()

    def __enter__(self) -> Oversight:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def qualification(self) -> Qualification:
        """Qualification sessions for this queue: seeded-defect tests of a reviewer."""
        return Qualification(self)

    @property
    def planted(self) -> PlantedLibrary:
        """The planted-error library, for QA administration. Not for the review application."""
        return PlantedLibrary(self._planted, self._config, self._clock)

    def gate_status(self) -> GateStatus:
        """For QA: whether this queue has enough planted errors."""
        return self._gate.status(self._queue.name)

    # ------------------------------------------------------------- submit

    def submit(
        self, item: Item, reviewer: str | None = None, key: str | None = None
    ) -> list[Ticket]:
        """Register a real item. Returns its ticket, and sometimes a second one, in random order.

        Queue **every** ticket and show ``ticket.payload``. Pass ``reviewer`` only if all of
        them will go to that person. Never show reviewers which items arrived together.

        ``key`` names one proposed action, for code that may run again (ADR-005). Repeating a
        call with the same key, target, payload and reviewer returns the same tickets, records
        nothing and does not ask the gate again; the first call's trace is the one kept. Queue
        the tickets after **every** call, ignoring any token your queue already has. The same
        key with anything else is refused.
        """
        if reviewer is not None:
            require_reviewer(reviewer)
        if key is not None:
            require_key(key)
        payload = plain_json(item.payload, "payload")
        payload_salt = new_salt()
        trace = trace_records(item.trace, payload_salt)
        self._check_target(item.target_id, payload)
        schema = self._item_schema()
        self._warn_if_unlike_schema(schema, payload)

        # The log's write lock is taken first. If the log is unavailable, the submission fails
        # here, before the gate has used up a planted error.
        with self._log.transaction():
            if key is not None:
                earlier = self._keys().replay(key, item.target_id, payload, reviewer, schema)
                if earlier is not None:
                    return earlier
            real = self._register_real(item.target_id, payload, payload_salt, trace)
            entries = [real]
            # Without the schema, fields could not be ordered alike, so nothing is planted.
            planted = self._ask_gate(reviewer) if schema else None
            if planted is not None:
                entries.append(planted)
            # The order of rows and events must not show which of a pair is which.
            self._rng.shuffle(entries)
            if key is not None:
                entries = self._keep_key(key, reviewer, entries)
            now = self._clock()
            for entry in entries:
                self._write(entry, schema, now)
        return [
            Ticket(entry.token, schema.arrange(entry.payload) if schema else entry.payload)
            for entry in entries
        ]

    def _keep_key(
        self, key: str, reviewer: str | None, entries: list[_Submission]
    ) -> list[_Submission]:
        """Give each item a token that can be rebuilt, and note the key."""
        seed = secrets.token_bytes(32)
        keyed = [replace(e, token=derive_token(seed, e.item_id)) for e in entries]
        for entry in keyed:
            add_key(self._planted, self._queue.name, key, entry.item_id, seed, reviewer)
        return keyed

    def _register_real(
        self,
        target_id: str,
        payload: dict[str, Any],
        payload_salt: bytes,
        trace: list[dict[str, str | None]],
    ) -> _Submission:
        item_id, salt = new_item_id(), new_salt()
        # Committed before the item exists in the log (ADR-001).
        add_real_item(self._planted, item_id, target_id, salt, payload_salt, trace)
        commitment = planted_commitment(item_id, ItemKind.REAL, None, target_id, salt)
        digest = trace_digest(trace, payload_salt)
        return _Submission(item_id, new_token(), payload, payload_salt, commitment, digest)

    def _ask_gate(self, reviewer: str | None) -> _Submission | None:
        """A fault in the planted-error side must not hold up real work."""
        try:
            injection = self._gate.on_submission(self._queue.name, reviewer)
        except (StoreError, sqlite3.Error) as error:
            # Only the type: the message could name a planted error in the host's logs.
            _log.error(
                "the gate failed for queue %r (%s). The item was accepted without a check of "
                "the planted-error library; run the gate status check.",
                self._queue.name,
                type(error).__name__,
            )
            return None
        if injection is None:
            return None
        # Copied the same way as a real payload.
        payload = plain_json(injection.planted.payload, "payload")
        return _Submission(
            injection.item_id,
            new_token(),
            payload,
            injection.payload_salt,
            injection.commitment,
            # A planted error has no trace. Its hash looks like any other.
            trace_digest([], injection.payload_salt),
        )

    def _write(self, entry: _Submission, schema: ItemSchema | None, now: str) -> None:
        digest = payload_hash(entry.payload, entry.payload_salt)
        self._db.execute(
            "INSERT INTO items (item_id, queue_id, system_id, token_hash, payload_hash, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                entry.item_id,
                self._queue.name,
                self._system_id,
                token_hash(entry.token),
                digest,
                now,
            ),
        )
        names = schema.property_names if schema else ()
        self._log.append(
            EventType.SUBMITTED,
            system_id=self._system_id,
            queue_id=self._queue.name,
            item_id=entry.item_id,
            data={
                "commitment": entry.commitment,
                "payload_hash": digest,
                "field_hashes": field_hashes(entry.payload, entry.payload_salt, names),
                "trace_hash": entry.trace_hash,
            },
        )

    def _check_target(self, target_id: str, payload: dict[str, Any]) -> None:
        prefix = self._config.reserved_target_prefix.casefold()
        if target_id.strip().casefold().startswith(prefix):
            raise OversightError(
                "target_id is in the range reserved for planted errors. Check "
                "reserved_target_prefix."
            )
        if target_id in _strings(payload):
            raise OversightError(
                "the payload contains the target_id. Keep target IDs out of the item, so that "
                "no review screen shows them."
            )

    def _item_schema(self) -> ItemSchema | None:
        try:
            return ItemSchema.load(self._queue.item_schema)
        except SchemaError as error:
            _log.error("the item schema for queue %r cannot be used: %s", self._queue.name, error)
            return None

    def _warn_if_unlike_schema(self, schema: ItemSchema | None, payload: dict[str, Any]) -> None:
        # Accepted anyway: real work is never refused for this. But planted errors follow the
        # schema, so real items that do not would make them stand out.
        problems = schema.errors(payload) if schema else []
        if problems:
            _log.warning(
                "an item submitted to queue %r does not match its item schema, which can make "
                "planted errors easier to spot: %s",
                self._queue.name,
                "; ".join(problems[:5]),
            )

    # ------------------------------------------------------------- review

    def shown(self, token: str, reviewer: str, context: Mapping[str, Any] | None = None) -> None:
        """Record that a reviewer's screen showed the item. May happen more than once.

        ``context`` names what the screen showed: ``{"sources": [...], "steps": [...],
        "fields": [...]}``. Pass names only, never content. **Everything a reviewer sees about
        an item must come from its ticket's payload**, because a planted item has nothing else.
        """
        require_reviewer(reviewer)
        manifest = review_manifest(context)
        with self._log.transaction():
            item = self._records.find(token)
            if self._records.reviewers_of(item.item_id, EventType.DECIDED):
                raise TransitionError("this item has already been decided")
            salt = read_entry(self._planted, item.item_id).payload_salt
            digest = context_digest(manifest, salt)
            if manifest is not None:
                self._store_context(item, reviewer, manifest, digest)
            self._db.execute(
                "INSERT OR IGNORE INTO reviewers (reviewer_id) VALUES (?)", (reviewer,)
            )
            self._records.append(EventType.SHOWN, item, reviewer, data={"context_hash": digest})

    def _store_context(
        self, item: ItemRef, reviewer: str, manifest: dict[str, list[str]], digest: str
    ) -> None:
        values = [json.dumps(manifest[column.removeprefix("shown_")]) for column in CONTEXT_COLUMNS]
        try:
            self._planted.execute(
                "INSERT INTO review_context (item_id, reviewer_id, shown_sources, "
                "shown_steps, shown_fields, manifest_hash, ts_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item.item_id, reviewer, *values, digest, self._clock()),
            )
        except sqlite3.Error as error:
            raise StoreError(f"cannot record the review context: {error}") from error

    def decided(
        self,
        token: str,
        reviewer: str,
        decision: Decision | str,
        edited_fields: Iterable[str] | None = None,
        final_payload: Mapping[str, Any] | None = None,
    ) -> None:
        """Record the one decision on an item, by a reviewer it was shown to.

        For ``edit``, pass the whole item as edited in ``final_payload``. ReviewProof works out
        which top-level fields changed. ``edited_fields`` is optional; if given, it must agree.
        """
        require_reviewer(reviewer)
        try:
            decision = Decision(decision)
        except ValueError:
            raise OversightError("decision must be approve, edit or reject") from None
        if decision is Decision.EDIT:
            if final_payload is None:
                raise OversightError("an edit needs final_payload: the whole item as edited")
            final = plain_json(final_payload, "final_payload")
        elif final_payload is not None or edited_fields:
            raise OversightError("only an edit takes final_payload or edited_fields")

        with self._log.transaction():
            item = self._records.find(token)
            if self._records.reviewers_of(item.item_id, EventType.DECIDED):
                raise TransitionError("this item has already been decided")
            if reviewer not in self._records.reviewers_of(item.item_id, EventType.SHOWN):
                raise TransitionError("record shown() for this reviewer before their decision")
            data = None
            if decision is Decision.EDIT:
                data = self._describe_edit(item, final, edited_fields)
            self._records.append(EventType.DECIDED, item, reviewer, decision, data)

    def _describe_edit(
        self, item: ItemRef, final: dict[str, Any], declared: Iterable[str] | None
    ) -> dict[str, Any]:
        before = self._submitted_field_hashes(item.item_id)
        salt = read_entry(self._planted, item.item_id).payload_salt
        after = field_hashes(final, salt, before)
        absent = field_hashes({}, salt, after)
        changed = sorted(name for name in after if after[name] != before.get(name, absent[name]))
        if not changed:
            raise OversightError("final_payload is unchanged; record the decision as approve")
        if declared is not None and (isinstance(declared, str) or set(declared) != set(changed)):
            raise OversightError(f"edited_fields does not match what changed: {', '.join(changed)}")
        return {"edited_fields": changed, "final_payload_hash": payload_hash(final, salt)}

    # ------------------------------------------------------------- execute and feedback

    def execute(
        self, token: str, payload: Mapping[str, Any], executor: Executor
    ) -> ExecutionResult:
        """**The only way to act on an item.** Call it for every approved or edited ticket,
        with the payload as approved. ``executor(target_id, payload)`` does the work.

        Any exception means nothing ran, except ``UnrecordedExecutionError`` (it ran, do not
        repeat) and errors raised by your executor (it may have run in part). An item is never
        executed twice.
        """
        return self._guard().execute(token, payload, executor)

    # ------------------------------------------------------------- paused agents (ADR-005)

    def settle(self, token: str, payload: Mapping[str, Any] | None = None) -> CallStatus:
        """Call after **every** decision on a keyed item, the same way for every ticket.

        Pass the payload as decided: the ticket's payload for an approval, the edited one for
        an edit. A rejection needs none. For a planted item this does what ``execute()`` does,
        without anything to run. Repeating it is harmless.

        Returns the state of the **real** action under the same key. Resume the paused call
        once it is no longer ``waiting``. It tells your server code nothing that
        ``execute()`` would not, and **none of it may reach a reviewer**.
        """
        with self._log.transaction():
            item = self._records.find(token)
            key = key_of(self._planted, self._queue.name, item.item_id)
            if key is None:
                raise OversightError("this item was not submitted with a key; use execute()")
            decision = self._decision_on(item.item_id)
        if decision is not Decision.REJECT:
            if payload is None:
                raise OversightError("an approval or edit needs the payload as decided")
            self._guard().settle(token, payload)
        return self.call_status(key)

    def call_status(self, key: str) -> CallStatus:
        """Whether the real action under ``key`` is still waiting, or how it was decided."""
        require_key(key)
        with self._log.transaction():
            return self._keys().status(key)

    def execute_call(
        self, key: str, payload: Mapping[str, Any], executor: Executor
    ) -> ExecutionResult:
        """``execute()`` for the real action under ``key``, with every one of its checks.

        This is what a resumed agent call uses. Nothing the agent library says matters here:
        without a recorded approval or edit of exactly this payload, nothing runs, and an
        action never runs twice.
        """
        require_key(key)
        try:
            with self._log.transaction():
                token = self._keys().real_token(key)
        except OversightError:
            raise
        except Exception as error:  # whatever went wrong, nothing runs
            name = type(error).__name__
            _log.error("a guard check failed in queue %r (%s)", self._queue.name, name)
            raise GuardError(
                "A safety check could not be completed. Nothing was executed. Run rp verify."
            ) from None
        return self.execute(token, payload, executor)

    def _decision_on(self, item_id: str) -> Decision:
        decisions = [
            e for e in item_events(self._db, item_id) if e["type"] == EventType.DECIDED.value
        ]
        if not decisions:
            raise TransitionError("this item has no decision")
        try:
            return Decision(decisions[0]["decision"])
        except ValueError as error:
            raise StoreError("the log holds an unknown decision; run rp verify") from error

    def _keys(self) -> KeyedSubmissions:
        return KeyedSubmissions(self._records, self._planted, self.planted)

    def _guard(self) -> Guard:
        return Guard(self._records, self._planted, self.planted, self._config, self._files)

    def feedback(self, reviewer: str, batch: bool = False) -> list[feedback.Feedback]:
        """Outcomes of planted errors ``reviewer`` has decided on, each delivered once.

        On a queue set to ``immediate``, call this after each decision. On a ``batched`` queue
        it returns nothing unless ``batch=True``, which your scheduled job passes.
        """
        require_reviewer(reviewer)
        if self._queue.feedback is FeedbackTiming.BATCHED and not batch:
            return []
        return feedback.collect(self._records, self._planted, self.planted, reviewer)

    def gate(
        self,
        *,
        enqueue: Callable[[Ticket], None],
        target_id: Callable[..., str],
        reviewer: Callable[..., str | None] | None = None,
    ) -> Callable[[Callable[..., Mapping[str, Any]]], Callable[..., None]]:
        """Decorator for a function that returns a proposal. See ``adapters.gate``."""
        return adapters.gate(self, enqueue=enqueue, target_id=target_id, reviewer=reviewer)

    # ------------------------------------------------------------- oversight context

    def register_system(
        self,
        *,
        model_id: str | None = None,
        model_version: str | None = None,
        prompt_version: str | None = None,
        tool_schema_hash: str | None = None,
        intended_use: str | None = None,
    ) -> list[str]:
        """Record that the AI system changed, and flag its reviewers for requalification.

        Returns the flagged reviewer IDs. Items submitted before keep their old context.
        **Update the settings file too:** the next time ReviewProof opens, the file wins.
        """
        changes = {
            name: value
            for name, value in {
                "model_id": model_id,
                "model_version": model_version,
                "prompt_version": prompt_version,
                "tool_schema_hash": tool_schema_hash,
                "intended_use": intended_use,
            }.items()
            if value is not None
        }
        try:
            context = replace(self._context, **changes)
        except (TypeError, ValueError) as error:
            raise OversightError(str(error)) from error
        if context.fingerprint == self._context.fingerprint:
            raise OversightError(
                "nothing that identifies the context changed: pass a new model, prompt or "
                "tool-schema value"
            )
        self._system_id, flagged = self._record_context(context)
        self._context = context
        return flagged

    def _record_context(self, context: OversightContext) -> tuple[str, list[str]]:
        """Put the queue and its context on record. If the queue's context has changed, or a
        system gets a new one, flag the reviewers who worked under another.
        """
        with self._log.transaction():
            row = self._db.execute(
                "SELECT system_id FROM systems WHERE fingerprint = ?", (context.fingerprint,)
            ).fetchone()
            system_id = new_item_id() if row is None else row[0]
            identity = {
                "model_id": context.model_id,
                "model_version": context.model_version,
                "prompt_version": context.prompt_version,
                "tool_schema_hash": context.tool_schema_hash,
            }
            if row is None:
                self._db.execute(
                    "INSERT INTO systems (system_id, fingerprint, name, model_id, "
                    "model_version, prompt_version, tool_schema_hash, intended_use, "
                    "registered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        system_id,
                        context.fingerprint,
                        context.system,
                        *identity.values(),
                        context.intended_use,
                        self._clock(),
                    ),
                )
            current = self._db.execute(
                "SELECT system_id FROM queues WHERE queue_id = ?", (self._queue.name,)
            ).fetchone()
            previous = None if current is None else current[0]
            moved = previous is not None and previous != system_id
            flagged: list[str] = []
            if row is None or moved:
                flagged = self._flag_for_requalification(context.system, system_id)
                self._log.append(
                    EventType.CONTEXT_REGISTERED,
                    system_id=system_id,
                    queue_id=self._queue.name,
                    data={
                        "system": context.system,
                        **identity,
                        "previous_system_id": previous,
                        "requalification_required": flagged,
                    },
                )
            self._save_queue(system_id)
        if flagged:
            _log.warning(
                "the AI system behind queue %r changed; %d reviewer(s) need requalification",
                self._queue.name,
                len(flagged),
            )
        return system_id, flagged

    def _flag_for_requalification(self, system_name: str, system_id: str) -> list[str]:
        """Everyone who reviewed or qualified under another context of this system."""
        rows = self._db.execute(
            "SELECT e.reviewer_id FROM events e JOIN systems s ON s.system_id = e.system_id "
            "WHERE s.name = ?1 AND s.system_id <> ?2 AND e.reviewer_id IS NOT NULL "
            "UNION "
            "SELECT q.reviewer_id FROM qualifications q "
            "JOIN systems s ON s.system_id = q.system_id "
            "WHERE s.name = ?1 AND s.system_id <> ?2",
            (system_name, system_id),
        ).fetchall()
        reviewers = sorted(row[0] for row in rows)
        for reviewer in reviewers:
            self._db.execute(
                "UPDATE reviewers SET qualification_status = ? WHERE reviewer_id = ?",
                (QualificationStatus.REQUALIFICATION_REQUIRED.value, reviewer),
            )
        return reviewers

    # ------------------------------------------------------------- storage

    def _save_queue(self, system_id: str) -> None:
        queue = self._queue
        category = queue.vital_adjusted_category
        values = {
            "queue_id": queue.name,
            "system_id": system_id,
            "task": queue.task,
            "planted_rate": queue.planted_rate,
            "min_review_seconds": queue.min_review_seconds,
            "vital_base_risk": queue.vital_base_risk,
            "vital_adjusted_category": None if category is None else category.value,
            "gxp": int(queue.gxp),
            "feedback": queue.feedback.value,
            "site": queue.site,
        }
        updates = ", ".join(f"{name} = excluded.{name}" for name in values)
        self._db.execute(
            f"INSERT INTO queues ({', '.join(values)}) "
            f"VALUES ({', '.join('?' * len(values))}) "
            f"ON CONFLICT (queue_id) DO UPDATE SET {updates}",
            tuple(values.values()),
        )

    def _submitted_field_hashes(self, item_id: str) -> dict[str, str]:
        for event in item_events(self._db, item_id):
            if event["type"] == EventType.SUBMITTED.value:
                hashes = event["data"].get("field_hashes")
                if isinstance(hashes, dict):
                    return hashes
        raise StoreError("the log has no usable submission record for this item")


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for element in value.values():
            yield from _strings(element)
    elif isinstance(value, list):
        for element in value:
            yield from _strings(element)
