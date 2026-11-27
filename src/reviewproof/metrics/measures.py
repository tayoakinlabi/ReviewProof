"""The metrics (steps 2.3 and 2.4). Each definition is given where it is calculated.

A metric is calculated over the **decided** items in its scope, for example one queue in one
period. Sessions and positions are worked out from each reviewer's whole history first, because
tiredness does not stop at the edge of a queue or a report period.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import median

from reviewproof.feedback import Outcome
from reviewproof.metrics.projection import ItemRecord
from reviewproof.metrics.stats import Rate
from reviewproof.models import Decision, DefectClass

#: (label, first position, last position)
POSITION_BUCKETS = (("1-10", 1, 10), ("11-25", 11, 25), ("26-50", 26, 50), ("51+", 51, None))
#: Hour of the session: 1 is its first hour. The last bucket takes everything later.
HOUR_BUCKETS = ("1st hour", "2nd hour", "3rd hour", "4th hour or later")


@dataclass(frozen=True)
class Place:
    """Where a decision fell in its reviewer's working pattern."""

    session: int
    position: int
    session_size: int
    hour: int

    @property
    def early(self) -> bool:
        """In the first half of its session. The middle item of an odd number counts."""
        return self.position * 2 <= self.session_size + 1


def sessions(records: Iterable[ItemRecord], gap_minutes: Mapping[str, int]) -> dict[str, Place]:
    """Place every decided item, keyed by item ID.

    A session is a run of one reviewer's decisions with no pause longer than the queue's
    ``session_gap_minutes`` between one decision and the next. Its hours are counted from when
    its first item was shown. ReviewProof does not know about shifts, so the session stands in
    for one.
    """
    by_reviewer: dict[str, list[ItemRecord]] = defaultdict(list)
    for record in records:
        if record.decided:
            by_reviewer[record.reviewer].append(record)

    places: dict[str, Place] = {}
    for decided in by_reviewer.values():
        decided.sort(key=lambda r: (r.decided_at, r.decided_event))
        runs: list[list[ItemRecord]] = []
        for record in decided:
            gap = timedelta(minutes=gap_minutes[record.queue])
            if runs and record.decided_at - runs[-1][-1].decided_at <= gap:
                runs[-1].append(record)
            else:
                runs.append([record])
        for number, run in enumerate(runs, start=1):
            start = run[0].first_shown_at or run[0].decided_at
            for position, record in enumerate(run, start=1):
                hour = int((record.decided_at - start).total_seconds() // 3600) + 1
                places[record.item_id] = Place(number, position, len(run), hour)
    return places


def scored(records: Iterable[ItemRecord]) -> list[ItemRecord]:
    """The planted items that count towards scores, in the order they were decided: decided,
    and the decider's first sight of that planted error.
    """
    counted = [r for r in records if r.decided and r.planted and r.first_sight]
    return sorted(counted, key=lambda r: (r.decided_at, r.decided_event))


def catch_rate(records: Iterable[ItemRecord]) -> Rate:
    """Planted errors caught out of planted errors decided on at first sight."""
    return Rate.of([r.outcome is Outcome.CATCH for r in scored(records)])


def by_defect_class(records: Sequence[ItemRecord]) -> dict[DefectClass, Rate]:
    """The catch rate for each class that has been decided on."""
    counted = scored(records)
    return {
        defect: Rate.of([r.outcome is Outcome.CATCH for r in counted if r.defect_class is defect])
        for defect in DefectClass
        if any(r.defect_class is defect for r in counted)
    }


def fast_approvals(records: Iterable[ItemRecord], min_seconds: Mapping[str, float]) -> Rate:
    """Approvals made faster than the queue's minimum review time, out of all approvals.
    Planted items count: this measures how the reviewer behaved.
    """
    return Rate.of(
        [
            r.dwell_ms < min_seconds[r.queue] * 1000
            for r in records
            if r.decision is Decision.APPROVE
        ]
    )


def median_dwell_ms(records: Iterable[ItemRecord]) -> float | None:
    """The middle time from shown to decided, over all decided items."""
    times = [r.dwell_ms for r in records if r.decided]
    return median(times) if times else None


def agreement_rate(records: Iterable[ItemRecord]) -> Rate:
    """Real AI proposals accepted unchanged, out of real proposals decided. Planted items are
    left out: they are not the AI's proposals.
    """
    return Rate.of([r.decision is Decision.APPROVE for r in records if r.decided and not r.planted])


@dataclass(frozen=True)
class Load:
    """How much each reviewer decided. Planted items count: they are work too."""

    reviewers: int
    reviewer_days: int
    median_per_day: float | None
    max_per_day: int
    sessions: int
    median_per_session: float | None
    max_per_session: int


def load(records: Iterable[ItemRecord], places: Mapping[str, Place]) -> Load:
    """Days are UTC calendar days."""
    per_day: dict[tuple, int] = defaultdict(int)
    per_session: dict[tuple, int] = defaultdict(int)
    for record in records:
        if record.decided:
            per_day[record.reviewer, record.decided_at.date()] += 1
            per_session[record.reviewer, places[record.item_id].session] += 1
    return Load(
        reviewers=len({reviewer for reviewer, _ in per_day}),
        reviewer_days=len(per_day),
        median_per_day=median(per_day.values()) if per_day else None,
        max_per_day=max(per_day.values(), default=0),
        sessions=len(per_session),
        median_per_session=median(per_session.values()) if per_session else None,
        max_per_session=max(per_session.values(), default=0),
    )


@dataclass(frozen=True)
class Fatigue:
    """The catch rate according to how far into a session the planted error came."""

    early: Rate
    late: Rate
    by_position: dict[str, Rate] = field(default_factory=dict)
    by_hour: dict[str, Rate] = field(default_factory=dict)

    @property
    def drop(self) -> float | None:
        """Early catch rate minus late. Positive means attention faded."""
        if self.early.value is None or self.late.value is None:
            return None
        return self.early.value - self.late.value


def fatigue(records: Sequence[ItemRecord], places: Mapping[str, Place]) -> Fatigue:
    counted = [(r.outcome is Outcome.CATCH, places[r.item_id]) for r in scored(records)]
    by_position = {
        label: Rate.of(
            [
                caught
                for caught, place in counted
                if first <= place.position and (last is None or place.position <= last)
            ]
        )
        for label, first, last in POSITION_BUCKETS
    }
    by_hour = {
        label: Rate.of(
            [caught for caught, place in counted if min(place.hour, len(HOUR_BUCKETS)) == number]
        )
        for number, label in enumerate(HOUR_BUCKETS, start=1)
    }
    return Fatigue(
        early=Rate.of([caught for caught, place in counted if place.early]),
        late=Rate.of([caught for caught, place in counted if not place.early]),
        by_position={k: v for k, v in by_position.items() if v.total},
        by_hour={k: v for k, v in by_hour.items() if v.total},
    )


@dataclass(frozen=True)
class Summary:
    """Every metric over one scope."""

    decided: int
    decided_real: int
    catch: Rate
    classes: dict[DefectClass, Rate]
    fast: Rate
    dwell_ms: float | None
    agreement: Rate
    load: Load
    fatigue: Fatigue
    #: Second and later sights of a planted error. Not part of any score.
    repeats: Rate
    #: Planted items shown to someone but not yet decided. Not counted as misses.
    pending_planted: int


def summarise(
    records: Sequence[ItemRecord],
    places: Mapping[str, Place],
    min_seconds: Mapping[str, float],
) -> Summary:
    decided = [r for r in records if r.decided]
    return Summary(
        decided=len(decided),
        decided_real=sum(not r.planted for r in decided),
        catch=catch_rate(decided),
        classes=by_defect_class(decided),
        fast=fast_approvals(decided, min_seconds),
        dwell_ms=median_dwell_ms(decided),
        agreement=agreement_rate(decided),
        load=load(decided, places),
        fatigue=fatigue(decided, places),
        repeats=Rate.of(
            [r.outcome is Outcome.CATCH for r in decided if r.planted and not r.first_sight]
        ),
        pending_planted=sum(r.planted and not r.decided and bool(r.showings) for r in records),
    )
