"""Voiding a faulty planted error (ADR-008).

Voiding says that a planted error's results measure nothing. It covers every copy and every
reviewer, catches and misses alike. Nothing is scored again or deleted, it cannot be undone, and
the guard is unchanged: a voided planted item is still never written.

**The rule behind the checks:** voiding must never make results look better without saying so.

ReviewProof has no sign-in, so it cannot prove who is typing. It checks only that the names it
is given differ as the rules require. Your procedures and signatures carry the rest.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from typing import TYPE_CHECKING, Any

from reviewproof.errors import OversightError
from reviewproof.models import EventType, QualificationStatus, VoidReason, new_item_id
from reviewproof.planted import PlantedError, UnknownPlantedError, _person
from reviewproof.store.commitments import new_salt
from reviewproof.store.eventlog import item_events, session_events, voids_in_effect
from reviewproof.store.schema import StoreError
from reviewproof.store.void_check import Void, commitment_for, read_voids

if TYPE_CHECKING:
    from reviewproof.api import Oversight

__all__ = ["Void", "VoidError", "VoidReason", "VoidState", "Voids", "state_of"]


class VoidError(OversightError):
    """A void, or a step towards one, was refused. The message says why."""


class VoidState(str, Enum):
    IN_EFFECT = "in_effect"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    #: Confirmed, but the log has no entry: no effect until the confirmer confirms again.
    NOT_COMPLETED = "not_completed"
    CANCELLED = "cancelled"


def state_of(void: Void, in_effect: dict[str, Void]) -> VoidState:
    """``in_effect`` comes from the log: see ``voids_in_effect``."""
    if any(effective.void_id == void.void_id for effective in in_effect.values()):
        return VoidState.IN_EFFECT
    if void.cancelled_by is not None:
        return VoidState.CANCELLED
    if void.confirmed_by is not None:
        return VoidState.NOT_COMPLETED
    return VoidState.AWAITING_CONFIRMATION


class Voids:
    """One queue's voids. Get it from ``Oversight.voids()``. For QA, not the review
    application.
    """

    def __init__(self, oversight: Oversight) -> None:
        self._o = oversight

    # ------------------------------------------------------------- reading

    def in_effect(self) -> dict[str, Void]:
        """The voids the log has put in effect, by planted error ID, in every queue."""
        with self._o._log.transaction():
            return voids_in_effect(self._o._db, self._o._planted)

    def all(self) -> list[tuple[Void, VoidState]]:
        """Every request on this queue, with what became of it."""
        in_effect = self.in_effect()
        queue = self._o._queue.name
        ours = {p.planted_error_id for p in self._o.planted.all(queue)}
        return [
            (void, state_of(void, in_effect))
            for void in read_voids(self._o._planted)
            if void.planted_error_id in ours
        ]

    # ------------------------------------------------------------- the steps

    def request(
        self,
        planted_error_id: str,
        requested_by: str,
        reason: VoidReason | str,
        explanation: str,
        reference: str,
    ) -> Void:
        """Ask to void a planted error. **Nothing changes** until someone else confirms.

        ``explanation`` says what is wrong with it, and ``reference`` names your quality
        record. Both are kept in the restricted database only.
        """
        _name(requested_by, "requested_by")
        try:
            reason = VoidReason(reason)
        except ValueError:
            allowed = ", ".join(member.value for member in VoidReason)
            raise VoidError(
                f"reason must be one of: {allowed}. That a planted error was hard, widely "
                "missed or recognisable is not a reason: retire it instead."
            ) from None
        for value, what in ((explanation, "explanation"), (reference, "reference")):
            if not isinstance(value, str) or not value.strip():
                raise VoidError(f"{what} must not be empty")
        with self._o._log.transaction():
            planted = self._planted_error(planted_error_id)
            if not self._o.planted._used(planted_error_id):
                raise VoidError(
                    f"{planted_error_id} has never been released or put in a session, so it has "
                    "no results: retire it instead"
                )
            if self._pending(planted_error_id) is not None:
                raise VoidError("there is already a request: confirm it or cancel it")
            self._check_not_a_reviewer(planted, requested_by)
            void_id = new_item_id()
            with self._writing() as db:
                db.execute(
                    "INSERT INTO void_requests (void_id, planted_error_id, reason, explanation, "
                    "reference, requested_by, requested_at, salt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        void_id,
                        planted_error_id,
                        reason.value,
                        explanation.strip(),
                        reference.strip(),
                        requested_by.strip(),
                        self._o._clock(),
                        new_salt(),
                    ),
                )
        return self._read(void_id)

    def confirm(self, planted_error_id: str, confirmed_by: str) -> Void:
        """Confirm someone else's request, which puts the void in effect. **Permanent.**

        If an earlier confirmation could not be recorded in the log, the same person confirms
        again to complete it.
        """
        _name(confirmed_by, "confirmed_by")
        with self._o._log.transaction():
            planted = self._planted_error(planted_error_id)
            void = self._pending(planted_error_id)
            if void is None:
                raise VoidError("there is no request to confirm")
            if void.confirmed_by is not None and _person(void.confirmed_by) != _person(
                confirmed_by
            ):
                raise VoidError(
                    "someone else's confirmation was not completed: they must confirm again, "
                    "or the request must be cancelled"
                )
            self._check_people(planted, void.requested_by, confirmed_by)
            if void.confirmed_by is None:
                self._store_confirmation(planted, void, confirmed_by.strip())
                void = self._read(void.void_id)
            self._o._log.append(
                EventType.PLANTED_VOIDED,
                system_id=self._o._system_id,
                queue_id=self._o._queue.name,
                data={"commitment": commitment_for(self._o._planted, void)},
            )
            self._flag_lost_qualifications(planted_error_id)
        return void

    def cancel(self, planted_error_id: str, cancelled_by: str) -> Void:
        """Drop a request that is not in effect."""
        _name(cancelled_by, "cancelled_by")
        with self._o._log.transaction():
            self._planted_error(planted_error_id)
            void = self._pending(planted_error_id)
            if void is None:
                raise VoidError("there is no request to cancel")
            with self._writing() as db:
                db.execute(
                    "INSERT INTO void_cancellations (void_id, cancelled_by, cancelled_at) "
                    "VALUES (?, ?, ?)",
                    (void.void_id, cancelled_by.strip(), self._o._clock()),
                )
        return self._read(void.void_id)

    # ------------------------------------------------------------- rules

    def _planted_error(self, planted_error_id: str) -> PlantedError:
        try:
            planted = self._o.planted.get(planted_error_id)
        except UnknownPlantedError as error:
            raise VoidError(str(error)) from error
        if planted.queue != self._o._queue.name:
            raise VoidError(f"{planted_error_id} belongs to queue {planted.queue!r}")
        return planted

    def _pending(self, planted_error_id: str) -> Void | None:
        """The request that is neither cancelled nor in effect, if any."""
        in_effect = voids_in_effect(self._o._db, self._o._planted)
        if planted_error_id in in_effect:
            raise VoidError(f"{planted_error_id} is already voided, and a void is permanent")
        return next(
            (
                void
                for void in read_voids(self._o._planted)
                if void.planted_error_id == planted_error_id and void.cancelled_by is None
            ),
            None,
        )

    def _check_people(self, planted: PlantedError, requester: str, confirmer: str) -> None:
        if _person(requester) == _person(confirmer):
            raise VoidError("a different person must confirm the request")
        for name in (requester, confirmer):
            self._check_not_a_reviewer(planted, name)
        made_it = {
            _person(name)
            for name in (planted.author, planted.approved_by, planted.second_approved_by)
            if name
        }
        if {_person(requester), _person(confirmer)} <= made_it:
            raise VoidError(
                "at least one of the two people must be someone who neither wrote nor approved "
                "this planted error"
            )

    def _check_not_a_reviewer(self, planted: PlantedError, name: str) -> None:
        if _person(name) in self._reviewers_who_met(planted.planted_error_id):
            raise VoidError(
                f"{name} is recorded as a reviewer who was shown or decided on this planted "
                "error, so cannot take part in voiding it"
            )

    def _reviewers_who_met(self, planted_error_id: str) -> set[str]:
        met = set()
        for item_id in self._copies(planted_error_id):
            met |= {
                event["reviewer_id"]
                for event in item_events(self._o._db, item_id)
                if event["type"] in (EventType.SHOWN.value, EventType.DECIDED.value)
            }
        met |= set(self._sat_by(planted_error_id).values())
        return {_person(name) for name in met if name}

    def _copies(self, planted_error_id: str) -> list[str]:
        return self._column(
            "SELECT item_id FROM item_registry WHERE planted_error_id = ?", planted_error_id
        )

    def _sat_by(self, planted_error_id: str) -> dict[str, str]:
        """Who sat each session of this queue that holds the planted error."""
        sessions = self._column(
            "SELECT session_id FROM qualification_items WHERE planted_error_id = ?",
            planted_error_id,
        )
        found = {}
        for session_id in sessions:
            events = session_events(self._o._db, self._o._queue.name, session_id)
            if events:
                found[session_id] = events[0]["reviewer_id"]
        return found

    def _flag_lost_qualifications(self, planted_error_id: str) -> None:
        """Keep the summary column in step. ``Qualification.status`` is what decides."""
        qualification = self._o.qualification()
        for reviewer in sorted(set(self._sat_by(planted_error_id).values())):
            state = qualification.status(reviewer)
            if state.status is QualificationStatus.REQUALIFICATION_REQUIRED:
                self._o._db.execute(
                    "UPDATE reviewers SET qualification_status = ? WHERE reviewer_id = ?",
                    (state.status.value, reviewer),
                )

    # ------------------------------------------------------------- storage

    def _store_confirmation(self, planted: PlantedError, void: Void, confirmed_by: str) -> None:
        """Record the confirmation and retire the planted error, together."""
        now = self._o._clock()
        with self._writing() as db:
            db.execute(
                "INSERT INTO void_confirmations (void_id, confirmed_by, confirmed_at) "
                "VALUES (?, ?, ?)",
                (void.void_id, confirmed_by, now),
            )
            if planted.retired_at is None:
                db.execute(
                    "UPDATE planted_errors SET retired_at = ?, retired_by = ?, "
                    "retire_reason = ? WHERE planted_error_id = ?",
                    (now, confirmed_by, f"voided: {void.reason.value}", planted.planted_error_id),
                )

    def _read(self, void_id: str) -> Void:
        return next(void for void in read_voids(self._o._planted) if void.void_id == void_id)

    def _column(self, statement: str, argument: str) -> list[Any]:
        try:
            return [row[0] for row in self._o._planted.execute(statement, (argument,))]
        except sqlite3.Error as error:
            raise StoreError(f"cannot read the restricted database: {error}") from error

    @contextmanager
    def _writing(self) -> Iterator[sqlite3.Connection]:
        db = self._o._planted
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except sqlite3.Error as error:
            raise StoreError(f"cannot write the void record: {error}") from error
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")


def _name(value: Any, what: str) -> None:
    if not isinstance(value, str) or not _person(value):
        raise VoidError(f"{what} must be a non-empty name or ID")
