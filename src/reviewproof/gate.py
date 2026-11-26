"""The gate: when a planted error enters a queue, and which one (ADR-003).

**When.** With each real item submitted, the gate releases a planted error with probability
``p / (1 - p)``, so that planted items make up a share ``p`` of all items. Every draw is
independent: what happened before says nothing about what comes next, so there is no pattern to
learn. The price is that the share over a short stretch can differ noticeably from ``p``.

If a planted error is due and none can be used, it is not saved up, which would cause a burst
later. The miss is recorded, so that QA can see the queue ran short.

**Which.** Planted errors are reused. The gate takes only approved ones that still fit the
queue, never one with a copy still waiting for a decision, and, when the host says who will
review the item, never one that person has been given or shown. Among those it takes one of the
least used, at random.

Without a reviewer, the gate **cannot** prevent someone meeting a planted error twice. Scoring
must therefore count only a reviewer's first sight of each planted error.
"""

from __future__ import annotations

import random
import secrets
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from reviewproof.canonical import utc_now
from reviewproof.config import Config, QueueConfig
from reviewproof.models import EventType, ItemKind, new_item_id
from reviewproof.planted import PlantedError, PlantedLibrary
from reviewproof.store.commitments import (
    FINGERPRINT_COLUMNS,
    new_salt,
    planted_commitment,
    planted_fingerprint,
)
from reviewproof.store.schema import StoreError

_CHUNK = 500


class GateError(ValueError):
    """The gate was asked for something it cannot do."""


def due_probability(rate: float) -> float:
    """The chance per real item that gives planted items a share ``rate`` of all items."""
    return rate / (1 - rate)


def is_due(rate: float, rng: random.Random) -> bool:
    """One independent draw. It deliberately remembers nothing."""
    return rng.random() < due_probability(rate)


@dataclass(frozen=True)
class Injection:
    """A planted item the gate has registered. The caller records its submission.

    Everything but the IDs is secret, and none of it but the payload and a new token may reach
    the host.
    """

    item_id: str
    planted: PlantedError = field(repr=False)
    target_id: str = field(repr=False)
    salt: bytes = field(repr=False)
    payload_salt: bytes = field(repr=False)
    commitment: str = field(repr=False)


@dataclass(frozen=True)
class GateStatus:
    """For QA: whether a queue has enough planted errors to keep up its rate."""

    queue: str
    planted_rate: float
    #: Approved and still fitting the item format.
    usable: int
    #: Usable ones with no copy still waiting for a decision.
    free: int
    #: Times a planted error was due but none could be used. Above zero: the queue ran short.
    shortfalls: int
    #: Approved but held back, with the reasons.
    stale: dict[str, list[str]]


@dataclass(frozen=True)
class _Use:
    item_id: str
    planted_error_id: str
    assigned_reviewer: str | None


class Gate:
    """Everything it remembers is in the databases, so it carries on across processes."""

    def __init__(
        self,
        planted: sqlite3.Connection,
        oversight: sqlite3.Connection,
        config: Config,
        rng: random.Random | None = None,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self._db = planted
        self._oversight = oversight
        self._config = config
        # Unpredictable unless a test supplies its own.
        self._rng = rng if rng is not None else secrets.SystemRandom()
        self._clock = clock
        self._library = PlantedLibrary(planted, config, clock)

    def on_submission(self, queue: str, reviewer: str | None = None) -> Injection | None:
        """Call once for each real item submitted to ``queue``.

        Pass ``reviewer`` only if everything this submission returns will go to that person.
        When a planted item is returned, its registry row has already been committed.
        """
        settings = self._queue(queue)
        if reviewer is not None and (not isinstance(reviewer, str) or not reviewer.strip()):
            raise GateError("reviewer must be a non-empty ID, or None")
        if not is_due(settings.planted_rate, self._rng):
            return None
        with self._writing():
            chosen = self._choose(queue, reviewer)
            if chosen is None:
                self._db.execute(
                    "INSERT INTO gate_shortfalls (queue_id, ts_utc) VALUES (?, ?)",
                    (queue, self._clock()),
                )
                return None
            return self._register(chosen, reviewer)

    def status(self, queue: str) -> GateStatus:
        settings = self._queue(queue)
        usable = self._library.available(queue)
        busy = self._in_flight(self._uses(usable))
        try:
            shortfalls = self._db.execute(
                "SELECT COUNT(*) FROM gate_shortfalls WHERE queue_id = ?", (queue,)
            ).fetchone()[0]
        except sqlite3.Error as error:
            raise StoreError(f"cannot read the gate's state: {error}") from error
        return GateStatus(
            queue=queue,
            planted_rate=settings.planted_rate,
            usable=len(usable),
            free=len(usable) - len(busy),
            shortfalls=shortfalls,
            stale=self._library.stale(queue),
        )

    # ------------------------------------------------------------- choosing

    def _choose(self, queue: str, reviewer: str | None) -> PlantedError | None:
        usable = self._library.available(queue)
        uses = self._uses(usable)
        excluded = self._in_flight(uses)
        if reviewer is not None:
            excluded |= self._known_to(uses, reviewer)
        candidates = [p for p in usable if p.planted_error_id not in excluded]
        if not candidates:
            return None
        counts = {p.planted_error_id: 0 for p in candidates}
        for use in uses:
            if use.planted_error_id in counts:
                counts[use.planted_error_id] += 1
        fewest = min(counts.values())
        return self._rng.choice([p for p in candidates if counts[p.planted_error_id] == fewest])

    def _uses(self, planted: list[PlantedError]) -> list[_Use]:
        ids = [p.planted_error_id for p in planted]
        uses: list[_Use] = []
        try:
            for chunk in _chunks(ids):
                rows = self._db.execute(
                    "SELECT item_id, planted_error_id, assigned_reviewer FROM item_registry "
                    f"WHERE planted_error_id IN ({_marks(chunk)})",
                    chunk,
                )
                uses += [_Use(*row) for row in rows]
        except sqlite3.Error as error:
            raise StoreError(f"cannot read the item registry: {error}") from error
        return uses

    def _events(self, uses: list[_Use], types: tuple[EventType, ...]) -> set[tuple[str, str]]:
        """(item_id, reviewer_id) for the given items' events of the given types."""
        found: set[tuple[str, str]] = set()
        try:
            for chunk in _chunks([use.item_id for use in uses]):
                rows = self._oversight.execute(
                    "SELECT item_id, reviewer_id FROM events "
                    f"WHERE item_id IN ({_marks(chunk)}) AND type IN ({_marks(types)})",
                    (*chunk, *(t.value for t in types)),
                )
                found.update(tuple(row) for row in rows)
        except sqlite3.Error as error:
            raise StoreError(f"cannot read the event log: {error}") from error
        return found

    def _in_flight(self, uses: list[_Use]) -> set[str]:
        """Planted errors with a copy that has no decision yet.

        Two identical items waiting together would give both away. A registry row left by a
        crash never gets a decision, so its planted error stays held back: the safe direction.
        """
        decided = {item for item, _ in self._events(uses, (EventType.DECIDED,))}
        return {use.planted_error_id for use in uses if use.item_id not in decided}

    def _known_to(self, uses: list[_Use], reviewer: str) -> set[str]:
        """Planted errors this reviewer was given, shown or decided on."""
        events = self._events(uses, (EventType.SHOWN, EventType.DECIDED))
        met = {item for item, who in events if who == reviewer}
        return {
            use.planted_error_id
            for use in uses
            if use.assigned_reviewer == reviewer or use.item_id in met
        }

    # ------------------------------------------------------------- state

    def _register(self, planted: PlantedError, reviewer: str | None) -> Injection:
        row = self._db.execute(
            f"SELECT {', '.join(FINGERPRINT_COLUMNS)} FROM planted_errors "
            "WHERE planted_error_id = ?",
            (planted.planted_error_id,),
        ).fetchone()
        item_id, salt, payload_salt = new_item_id(), new_salt(), new_salt()
        self._db.execute(
            "INSERT INTO item_registry (item_id, kind, planted_error_id, target_id, salt, "
            "payload_salt, assigned_reviewer) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                ItemKind.PLANTED.value,
                planted.planted_error_id,
                planted.reserved_target_id,
                salt,
                payload_salt,
                reviewer,
            ),
        )
        commitment = planted_commitment(
            item_id,
            ItemKind.PLANTED,
            planted.planted_error_id,
            planted.reserved_target_id,
            salt,
            planted_fingerprint(dict(row)),
        )
        return Injection(
            item_id, planted, planted.reserved_target_id, salt, payload_salt, commitment
        )

    def _queue(self, queue: str) -> QueueConfig:
        settings = self._config.queues.get(queue)
        if settings is None:
            raise GateError(f"queue {queue!r} is not configured")
        return settings

    @contextmanager
    def _writing(self) -> Iterator[None]:
        """One write transaction: two processes cannot release the same planted error."""
        if self._db.in_transaction:
            raise StoreError("the planted-error connection is already in a transaction")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            yield
            self._db.execute("COMMIT")
        except sqlite3.Error as error:
            raise StoreError(f"cannot update the gate's state: {error}") from error
        finally:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")


def _chunks(values: list[str]) -> Iterator[list[str]]:
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


def _marks(values: object) -> str:
    return ", ".join("?" * len(values))
