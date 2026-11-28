"""Reviewer qualification: sessions of items with known answers, scored against criteria fixed
before they start (ADR-006).

A session mixes **planted errors from the qualification pool** with **correct items**. Without
correct items, someone who rejects everything would pass. Nothing in a session is executed.

The criteria and a commitment to every item go into the event log when the session starts.
Scoring reads them from there, so neither can be changed while it runs, and ``rp verify``
recomputes each result from the logged answers.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from reviewproof.canonical import canonical_json
from reviewproof.errors import OversightError, TransitionError, UnknownTokenError
from reviewproof.feedback import Outcome, score
from reviewproof.itemschema import ItemSchema, SchemaError
from reviewproof.models import (
    Decision,
    DefectClass,
    EventType,
    Purpose,
    QualificationStatus,
    Ticket,
    new_item_id,
    new_token,
)
from reviewproof.records import plain_json, require_reviewer, token_hash
from reviewproof.store.commitments import (
    field_hashes,
    judgment_hash,
    new_salt,
    qualification_commitment,
)
from reviewproof.store.eventlog import item_events, session_events, voids_in_effect
from reviewproof.store.registry import fingerprint_of
from reviewproof.store.schema import StoreError
from reviewproof.strictload import LoadError, load_file

if TYPE_CHECKING:
    from reviewproof.api import Oversight

#: Fewest correct items in a session, so that rejecting everything cannot pass.
MIN_CORRECT_ITEMS = 5


class PlanError(OversightError):
    """A session plan cannot be used. The message says why."""


@dataclass(frozen=True)
class Criteria:
    """The pass mark, as counts. Fixed when a session starts."""

    min_caught: int
    max_false_rejects: int


@dataclass(frozen=True)
class Plan:
    queue: str
    planted_errors: tuple[str, ...]
    correct_items: tuple[Mapping[str, Any], ...] = field(repr=False)
    criteria: Criteria


@dataclass(frozen=True)
class Session:
    """What the reviewer is given: an opaque token and a payload for each item, in random
    order. Nothing says which items are defective.
    """

    session_id: str
    reviewer: str
    items: tuple[Ticket, ...] = field(repr=False)


@dataclass(frozen=True)
class Result:
    """For QA. A person's score: handle it under your reviewer privacy policy."""

    session_id: str
    reviewer: str
    passed: bool
    caught: int
    defects: int
    #: Correct items the reviewer rejected or changed.
    false_rejects: int
    correct_items: int
    criteria: Criteria
    #: (caught, of) for each kind of defect in the session.
    by_class: Mapping[DefectClass, tuple[int, int]] = field(default_factory=dict)
    abandoned: bool = False


@dataclass(frozen=True)
class State:
    """Whether a reviewer is qualified for a queue's **current** version of the AI system."""

    status: QualificationStatus
    #: The passed session that counts, or the latest that once did.
    session_id: str | None = None
    passed_at: str | None = None
    #: In that session: how many planted errors have since been voided (ADR-008), and of
    #: those that still count, how many there are and how many the reviewer caught. The pass
    #: counts only while ``still_caught`` is at least ``mark``.
    voided: int = 0
    still_counting: int | None = None
    still_caught: int | None = None
    mark: int | None = None
    #: This reviewer's sessions on the queue that hold a voided planted error, passed or not.
    affected: tuple[str, ...] = ()


def load_plan(path: str | Path) -> Plan:
    """Load a YAML or JSON plan. ``correct_items`` names a JSON or YAML file beside it that
    holds a list of correct items, which must be synthetic or de-identified.
    """
    path = Path(path)
    try:
        data = load_file(path)
        if not isinstance(data, dict):
            raise PlanError("a plan must be a mapping of keys to values")
        required = {"queue", "planted_errors", "correct_items", "pass"}
        _keys(data, required, "plan")
        if not isinstance(data["correct_items"], str):
            raise PlanError("correct_items must name a file")
        correct = load_file(path.parent / data["correct_items"])
    except LoadError as error:
        raise PlanError(str(error)) from error
    criteria = data["pass"]
    if not isinstance(criteria, dict):
        raise PlanError("pass must hold min_caught and max_false_rejects")
    _keys(criteria, {"min_caught", "max_false_rejects"}, "pass")
    planted = data["planted_errors"]
    if not isinstance(planted, list) or not all(isinstance(p, str) for p in planted):
        raise PlanError("planted_errors must be a list of planted error IDs")
    if not isinstance(correct, list):
        raise PlanError("the correct_items file must hold a list of items")
    return Plan(data["queue"], tuple(planted), tuple(correct), Criteria(**criteria))


def _keys(data: Mapping, required: set[str], where: str) -> None:
    unknown = sorted(str(key) for key in data.keys() - required)
    if unknown:
        raise PlanError(f"{where}: unknown key(s): {', '.join(unknown)}")
    missing = sorted(required - data.keys())
    if missing:
        raise PlanError(f"{where}: missing key(s): {', '.join(missing)}")


def _still_met(standing: Mapping[str, Any]) -> bool:
    """A pass untouched by voids stands. Otherwise the original mark must still be met."""
    return not standing or standing["still_caught"] >= standing["mark"]


@dataclass(frozen=True)
class _Item:
    item_id: str
    session_id: str
    position: int
    planted_error_id: str | None
    payload: dict[str, Any]
    salt: bytes


class Qualification:
    """Sessions for one queue. Get it from ``Oversight.qualification()``."""

    def __init__(self, oversight: Oversight) -> None:
        self._o = oversight

    # ------------------------------------------------------------- starting

    def start(self, reviewer: str, plan: Plan) -> Session:
        require_reviewer(reviewer)
        queue = self._o._queue
        if plan.queue != queue.name:
            raise PlanError(f"the plan is for queue {plan.queue!r}, not {queue.name!r}")
        try:
            schema = ItemSchema.load(queue.item_schema)
        except SchemaError as error:
            raise PlanError(f"the item schema cannot be used: {error}") from error

        with self._o._log.transaction():
            if self._open_session(reviewer) is not None:
                raise TransitionError(
                    "this reviewer has an unfinished session for this queue: finish or "
                    "abandon it first"
                )
            entries = self._prepare(reviewer, plan, schema)
            session_id = new_item_id()
            self._o._rng.shuffle(entries)
            tickets, commitments = self._store(session_id, entries)
            self._append(
                EventType.QUALIFICATION_STARTED,
                reviewer,
                data={
                    "session_id": session_id,
                    "criteria": {
                        "min_caught": plan.criteria.min_caught,
                        "max_false_rejects": plan.criteria.max_false_rejects,
                    },
                    "items": commitments,
                },
            )
            self._o._db.execute(
                "INSERT OR IGNORE INTO reviewers (reviewer_id) VALUES (?)", (reviewer,)
            )
        return Session(
            session_id,
            reviewer,
            tuple(Ticket(token, schema.arrange(payload)) for token, payload in tickets),
        )

    def _prepare(
        self, reviewer: str, plan: Plan, schema: ItemSchema
    ) -> list[tuple[str | None, dict[str, Any]]]:
        """Check the plan. Returns (planted error ID or None, payload) for each item."""
        floor = self._o._queue.thresholds.individual_min_planted
        defects, correct = len(plan.planted_errors), len(plan.correct_items)
        if len(set(plan.planted_errors)) != defects:
            raise PlanError("a planted error is listed twice")
        if defects < floor:
            raise PlanError(
                f"a session needs at least {floor} planted errors, because its result is an "
                f"individual score; the plan has {defects}"
            )
        if correct < MIN_CORRECT_ITEMS:
            raise PlanError(
                f"a session needs at least {MIN_CORRECT_ITEMS} correct items, so that "
                "rejecting everything cannot pass"
            )
        criteria = plan.criteria
        for name, value in vars(criteria).items():
            if type(value) is not int:
                raise PlanError(f"pass.{name} must be a whole number")
        if not 1 <= criteria.min_caught <= defects:
            raise PlanError(f"pass.min_caught must be from 1 to {defects}")
        if not 0 <= criteria.max_false_rejects <= correct // 2:
            raise PlanError(
                f"pass.max_false_rejects must be from 0 to {correct // 2}: at most half of "
                "the correct items"
            )

        library = self._o.planted
        usable = {
            p.planted_error_id: p for p in library.available(plan.queue, Purpose.QUALIFICATION)
        }
        unusable = [p for p in plan.planted_errors if p not in usable]
        if unusable:
            raise PlanError(
                "not approved qualification planted errors that fit this queue: "
                f"{', '.join(unusable)}"
            )
        seen = set(plan.planted_errors) & self._seen_by(reviewer)
        if seen:
            raise PlanError(
                "this reviewer has met these in an earlier session; use new ones: "
                f"{', '.join(sorted(seen))}"
            )

        entries = [(p, dict(usable[p].payload)) for p in plan.planted_errors]
        used = {canonical_json(payload) for _, payload in entries}
        prefix = self._o._config.reserved_target_prefix.casefold()
        for number, item in enumerate(plan.correct_items):
            where = f"correct_items[{number}]"
            try:
                payload = plain_json(item, where)
            except OversightError as error:
                raise PlanError(str(error)) from error
            problems = schema.errors(payload, where=where)
            if problems:
                raise PlanError("; ".join(problems))
            text = canonical_json(payload)
            if text in used:
                raise PlanError(f"{where} is the same as another item in the session")
            if prefix in text.decode("utf-8").casefold():
                raise PlanError(f"{where} contains the reserved target prefix")
            used.add(text)
            entries.append((None, payload))
        return entries

    def _store(
        self, session_id: str, entries: Sequence[tuple[str | None, dict[str, Any]]]
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, str]]]:
        planted = self._o._planted
        tickets, commitments = [], []
        try:
            planted.execute("BEGIN IMMEDIATE")
            for position, (planted_error_id, payload) in enumerate(entries, start=1):
                item_id, token, salt = new_item_id(), new_token(), new_salt()
                text = canonical_json(payload).decode("utf-8")
                planted.execute(
                    "INSERT INTO qualification_items (item_id, session_id, position, "
                    "planted_error_id, token_hash, payload, salt) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        item_id,
                        session_id,
                        position,
                        planted_error_id,
                        token_hash(token),
                        text,
                        salt,
                    ),
                )
                fingerprint = (
                    None if planted_error_id is None else fingerprint_of(planted, planted_error_id)
                )
                commitments.append(
                    {
                        "item_id": item_id,
                        "commitment": qualification_commitment(
                            item_id,
                            session_id,
                            position,
                            planted_error_id,
                            text,
                            salt,
                            fingerprint,
                        ),
                    }
                )
                tickets.append((token, json.loads(text)))
            planted.execute("COMMIT")
        except sqlite3.Error as error:
            raise StoreError(f"cannot record the session: {error}") from error
        finally:
            if planted.in_transaction:
                planted.execute("ROLLBACK")
        return tickets, commitments

    # ------------------------------------------------------------- during

    def judged(self, token: str, reviewer: str, judgment: Mapping[str, Any]) -> None:
        """Record the reviewer's own judgment **before** the AI's answer is shown to them."""
        require_reviewer(reviewer)
        record = plain_json(judgment, "judgment")
        with self._o._log.transaction():
            item = self._open_item(token, reviewer)
            earlier = {e["type"] for e in item_events(self._o._db, item.item_id)}
            if earlier:
                raise TransitionError(
                    "the judgment must come first, once, before the item is shown"
                )
            text = canonical_json(record).decode("utf-8")
            try:
                self._o._planted.execute(
                    "INSERT INTO qualification_judgments (item_id, judgment, ts_utc) "
                    "VALUES (?, ?, ?)",
                    (item.item_id, text, self._o._clock()),
                )
            except sqlite3.Error as error:
                raise StoreError(f"cannot record the judgment: {error}") from error
            self._append(
                EventType.QUALIFICATION_JUDGED,
                reviewer,
                item_id=item.item_id,
                data={
                    "session_id": item.session_id,
                    "judgment_hash": judgment_hash(text, item.salt),
                },
            )

    def shown(self, token: str, reviewer: str) -> None:
        """Record that the item was on screen. Optional for offline sessions."""
        require_reviewer(reviewer)
        with self._o._log.transaction():
            item = self._open_item(token, reviewer)
            self._append(
                EventType.QUALIFICATION_SHOWN,
                reviewer,
                item_id=item.item_id,
                data={"session_id": item.session_id},
            )

    def answered(
        self,
        token: str,
        reviewer: str,
        decision: Decision | str,
        final_payload: Mapping[str, Any] | None = None,
    ) -> None:
        """Record the one answer to an item. Says nothing about whether it was right."""
        require_reviewer(reviewer)
        try:
            decision = Decision(decision)
        except ValueError:
            raise OversightError("decision must be approve, edit or reject") from None
        final = None
        if decision is Decision.EDIT:
            if final_payload is None:
                raise OversightError("an edit needs final_payload: the whole item as edited")
            final = plain_json(final_payload, "final_payload")
        elif final_payload is not None:
            raise OversightError("only an edit takes final_payload")

        with self._o._log.transaction():
            item = self._open_item(token, reviewer)
            if self._answer(item.item_id) is not None:
                raise TransitionError("this item has already been answered")
            data: dict[str, Any] = {"session_id": item.session_id}
            if final is not None:
                before = field_hashes(item.payload, item.salt)
                after = field_hashes(final, item.salt, before)
                absent = field_hashes({}, item.salt, after)
                changed = sorted(
                    name for name in after if after[name] != before.get(name, absent[name])
                )
                if not changed:
                    raise OversightError("final_payload is unchanged; record the answer as approve")
                data["edited_fields"] = changed
            self._append(
                EventType.QUALIFICATION_ANSWERED,
                reviewer,
                item_id=item.item_id,
                decision=decision,
                data=data,
            )

    # ------------------------------------------------------------- ending

    def finish(self, session_id: str) -> Result:
        """Score a session in which every item has been answered."""
        return self._end(session_id, abandoned=False)

    def abandon(self, session_id: str) -> Result:
        """End a session early. It counts as not passed, and its planted errors stay used."""
        return self._end(session_id, abandoned=True)

    def _end(self, session_id: str, abandoned: bool) -> Result:
        with self._o._log.transaction():
            events = self._session(session_id)
            started = events[0]
            if any(e["type"] == EventType.QUALIFICATION_FINISHED.value for e in events):
                raise TransitionError("this session has already ended")
            reviewer = started["reviewer_id"]
            answers = {
                e["item_id"]: e
                for e in events
                if e["type"] == EventType.QUALIFICATION_ANSWERED.value
            }
            items = [self._item(entry["item_id"]) for entry in started["data"]["items"]]
            self._check_unchanged(items, started["data"]["items"])
            remaining = sum(item.item_id not in answers for item in items)
            if remaining and not abandoned:
                raise TransitionError(f"{remaining} item(s) have not been answered yet")

            criteria = Criteria(**started["data"]["criteria"])
            result = self._score(session_id, reviewer, items, answers, criteria, abandoned)
            self._append(
                EventType.QUALIFICATION_FINISHED,
                reviewer,
                system_id=started["system_id"],
                data={
                    "session_id": session_id,
                    "passed": result.passed,
                    "abandoned": abandoned,
                    "caught": result.caught,
                    "defects": result.defects,
                    "false_rejects": result.false_rejects,
                    "correct_items": result.correct_items,
                },
            )
            self._o._db.execute(
                "INSERT INTO qualifications (session_id, reviewer_id, system_id, queue_id, "
                "score, passed, ts_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    reviewer,
                    started["system_id"],
                    self._o._queue.name,
                    result.caught / result.defects,
                    int(result.passed),
                    self._o._clock(),
                ),
            )
            # A planted error may have been voided while the session was open.
            voided = frozenset(voids_in_effect(self._o._db, self._o._planted))
            standing = self._standing(self._session(session_id), voided)
            if (
                result.passed
                and _still_met(standing)
                and started["system_id"] == self._o._system_id
            ):
                self._o._db.execute(
                    "UPDATE reviewers SET qualification_status = ? WHERE reviewer_id = ?",
                    (QualificationStatus.QUALIFIED.value, reviewer),
                )
        return result

    def _score(
        self,
        session_id: str,
        reviewer: str,
        items: Sequence[_Item],
        answers: Mapping[str, Mapping],
        criteria: Criteria,
        abandoned: bool,
    ) -> Result:
        caught = defects = false_rejects = correct = 0
        by_class: dict[DefectClass, list[int]] = {}
        library = self._o.planted
        for item in items:
            answer = answers.get(item.item_id)
            if item.planted_error_id is None:
                correct += 1
                if answer is not None and answer["decision"] != Decision.APPROVE.value:
                    false_rejects += 1
                continue
            defects += 1
            planted = library.get(item.planted_error_id)
            tally = by_class.setdefault(planted.defect_class, [0, 0])
            tally[1] += 1
            if answer is None:
                continue
            edited = answer["data"].get("edited_fields", [])
            if score(answer["decision"], edited, planted.defect_field) is Outcome.CATCH:
                caught += 1
                tally[0] += 1
        passed = (
            not abandoned
            and caught >= criteria.min_caught
            and false_rejects <= criteria.max_false_rejects
        )
        return Result(
            session_id=session_id,
            reviewer=reviewer,
            passed=passed,
            caught=caught,
            defects=defects,
            false_rejects=false_rejects,
            correct_items=correct,
            criteria=criteria,
            by_class={k: (v[0], v[1]) for k, v in by_class.items()},
            abandoned=abandoned,
        )

    # ------------------------------------------------------------- status

    def status(self, reviewer: str) -> State:
        """Worked out from the log: a pass counts only for the queue's current version of the
        AI system, only if the queue has not changed version since the session started, and
        only while its original mark is met without any voided planted error (ADR-008). The
        recorded result never changes, and a fail never becomes a pass.
        """
        require_reviewer(reviewer)
        queue = self._o._queue.name
        with self._o._log.transaction():
            changed = self._o._db.execute(
                "SELECT IFNULL(MAX(event_id), 0) FROM events WHERE type = ? AND queue_id = ?",
                (EventType.CONTEXT_REGISTERED.value, queue),
            ).fetchone()[0]
            voided = frozenset(voids_in_effect(self._o._db, self._o._planted))
            history = self._events_of(reviewer, queue)
            affected = tuple(
                e["data"]["session_id"]
                for e in history
                if e["type"] == EventType.QUALIFICATION_STARTED.value
                and voided & {self._item(i["item_id"]).planted_error_id for i in e["data"]["items"]}
            )
            earlier = None
            for finished in reversed(history):
                if finished["type"] != EventType.QUALIFICATION_FINISHED.value:
                    continue
                events = self._session(finished["data"]["session_id"])
                if not events[-1]["data"].get("passed"):
                    continue
                started = events[0]
                standing = self._standing(events, voided)
                found = dict(
                    session_id=finished["data"]["session_id"],
                    passed_at=events[-1]["ts_utc"],
                    affected=affected,
                    **standing,
                )
                current = started["system_id"] == self._o._system_id
                if current and started["event_id"] > changed and _still_met(standing):
                    return State(QualificationStatus.QUALIFIED, **found)
                earlier = earlier or found
        if earlier:
            return State(QualificationStatus.REQUALIFICATION_REQUIRED, **earlier)
        return State(QualificationStatus.NOT_QUALIFIED, affected=affected)

    def _standing(self, events: Sequence[Mapping], voided: frozenset[str]) -> dict[str, Any]:
        """What is left of a session's catches once voided planted errors are set aside."""
        if not voided:
            return {}
        started = events[0]
        items = [self._item(entry["item_id"]) for entry in started["data"]["items"]]
        # The records must still be what the log committed to before they decide anything,
        # even that no voided planted error is among them.
        self._check_unchanged(items, started["data"]["items"])
        if not voided & {item.planted_error_id for item in items}:
            return {}
        answers = {
            e["item_id"]: e for e in events if e["type"] == EventType.QUALIFICATION_ANSWERED.value
        }
        library = self._o.planted
        counting = [
            item
            for item in items
            if item.planted_error_id is not None and item.planted_error_id not in voided
        ]
        caught = 0
        for item in counting:
            answer = answers.get(item.item_id)
            if answer is not None:
                field_name = library.get(item.planted_error_id).defect_field
                edited = answer["data"].get("edited_fields", [])
                caught += score(answer["decision"], edited, field_name) is Outcome.CATCH
        return {
            "voided": sum(item.planted_error_id in voided for item in items),
            "still_counting": len(counting),
            "still_caught": caught,
            "mark": started["data"]["criteria"]["min_caught"],
        }

    # ------------------------------------------------------------- storage

    def _events_of(self, reviewer: str, queue: str) -> list[dict[str, Any]]:
        rows = self._o._db.execute(
            "SELECT type, data FROM events WHERE reviewer_id = ? AND queue_id = ? "
            "AND type IN (?, ?) ORDER BY event_id",
            (
                reviewer,
                queue,
                EventType.QUALIFICATION_STARTED.value,
                EventType.QUALIFICATION_FINISHED.value,
            ),
        ).fetchall()
        return [{"type": row[0], "data": json.loads(row[1])} for row in rows]

    def _open_session(self, reviewer: str) -> str | None:
        open_sessions: dict[str, bool] = {}
        for event in self._events_of(reviewer, self._o._queue.name):
            started = event["type"] == EventType.QUALIFICATION_STARTED.value
            open_sessions[event["data"]["session_id"]] = started
        return next((sid for sid, is_open in open_sessions.items() if is_open), None)

    def _seen_by(self, reviewer: str) -> set[str]:
        """Planted errors this reviewer was given in any earlier session, on any queue."""
        rows = self._o._db.execute(
            "SELECT data FROM events WHERE type = ? AND reviewer_id = ?",
            (EventType.QUALIFICATION_STARTED.value, reviewer),
        ).fetchall()
        ids = [item["item_id"] for row in rows for item in json.loads(row[0])["items"]]
        seen = set()
        for item_id in ids:
            planted_error_id = self._item(item_id).planted_error_id
            if planted_error_id:
                seen.add(planted_error_id)
        return seen

    def _session(self, session_id: str) -> list[dict[str, Any]]:
        """A session's events, each checked against the chain, starting with its start."""
        events = session_events(self._o._db, self._o._queue.name, str(session_id))
        if not events or events[0]["type"] != EventType.QUALIFICATION_STARTED.value:
            raise UnknownTokenError("no session with that ID in this queue")
        return events

    def _item(self, item_id: str) -> _Item:
        return self._read_item("item_id", item_id)

    def _read_item(self, column: str, value: str) -> _Item:
        try:
            row = self._o._planted.execute(
                "SELECT item_id, session_id, position, planted_error_id, payload, salt "
                f"FROM qualification_items WHERE {column} = ?",
                (value,),
            ).fetchone()
        except sqlite3.Error as error:
            raise StoreError(f"cannot read the session records: {error}") from error
        if row is None:
            raise UnknownTokenError("no session item has that token")
        try:
            return _Item(row[0], row[1], row[2], row[3], json.loads(row[4]), row[5])
        except ValueError as error:
            raise StoreError("a session record is damaged") from error

    def _open_item(self, token: str, reviewer: str) -> _Item:
        if not isinstance(token, str):
            raise UnknownTokenError("no session item has that token")
        item = self._read_item("token_hash", token_hash(token))
        events = self._session(item.session_id)
        if events[0]["reviewer_id"] != reviewer:
            raise TransitionError("this session belongs to another reviewer")
        if any(e["type"] == EventType.QUALIFICATION_FINISHED.value for e in events):
            raise TransitionError("this session has already ended")
        return item

    def _answer(self, item_id: str) -> Mapping | None:
        return next(
            (
                e
                for e in item_events(self._o._db, item_id)
                if e["type"] == EventType.QUALIFICATION_ANSWERED.value
            ),
            None,
        )

    def _check_unchanged(self, items: Sequence[_Item], committed: Sequence[Mapping]) -> None:
        for item, entry in zip(items, committed, strict=True):
            try:
                text = self._o._planted.execute(
                    "SELECT payload FROM qualification_items WHERE item_id = ?",
                    (item.item_id,),
                ).fetchone()[0]
                fingerprint = (
                    None
                    if item.planted_error_id is None
                    else fingerprint_of(self._o._planted, item.planted_error_id)
                )
                actual = qualification_commitment(
                    item.item_id,
                    item.session_id,
                    item.position,
                    item.planted_error_id,
                    text,
                    item.salt,
                    fingerprint,
                )
            except sqlite3.Error as error:
                raise StoreError(f"cannot read the session records: {error}") from error
            if actual != entry["commitment"]:
                raise StoreError(
                    "the session records do not match the log; run rp verify. Nothing was scored."
                )

    def _append(
        self,
        event_type: EventType,
        reviewer: str,
        *,
        data: Mapping[str, Any],
        item_id: str | None = None,
        decision: Decision | None = None,
        system_id: str | None = None,
    ) -> None:
        self._o._log.append(
            event_type,
            system_id=system_id or self._o._system_id,
            queue_id=self._o._queue.name,
            item_id=item_id,
            reviewer_id=reviewer,
            decision=decision,
            data=data,
        )
