"""The metrics (steps 2.3 and 2.4). Each definition is given where it is calculated.

A metric is calculated over the **decided** items in its scope, for example one queue in one
period. Sessions and positions are worked out from each reviewer's whole history first, because
tiredness does not stop at the edge of a queue or a report period.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
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


def _scorable(records: Iterable[ItemRecord]) -> list[ItemRecord]:
    """Planted items decided at the decider's first sight of that planted error, voided or
    not, in the order they were decided.
    """
    found = [r for r in records if r.decided and r.planted and r.first_sight]
    return sorted(found, key=lambda r: (r.decided_at, r.decided_event))


def scored(records: Iterable[ItemRecord]) -> list[ItemRecord]:
    """The planted items that count towards scores: ``_scorable`` ones whose planted error
    has not been voided (ADR-008).
    """
    return [r for r in _scorable(records) if not r.voided]


def left_out(records: Iterable[ItemRecord]) -> list[ItemRecord]:
    """The results that would count, had their planted errors not been voided."""
    return [r for r in _scorable(records) if r.voided]


def as_recorded(records: Sequence[ItemRecord]) -> list[ItemRecord]:
    """The records as if nothing had been voided, for the figures that must be shown beside
    the ones that leave voided results out.
    """
    return [replace(r, voided=None) if r.voided else r for r in records]


#: Given the decided items a figure is worked out from, says whether to withhold it.
Hides = Callable[[Sequence[ItemRecord]], bool]


def nothing_hidden(basis: Sequence[ItemRecord]) -> bool:
    return False


def one_reviewer_rule(scope: Iterable[ItemRecord], floor: int) -> Hides:
    """Protect one person within a group's figures (ADR-004). A figure worked out from the
    items of a single reviewer is that person's figure, so it is withheld unless ``scope``
    holds at least ``floor`` planted errors that they decided at first sight.

    The rule applies to every figure and every breakdown of it, each judged by its own items.
    It cannot stop someone working a hidden figure out by subtraction from the others.
    """
    planted = Counter(r.reviewer for r in scored(scope))

    def hides(basis: Sequence[ItemRecord]) -> bool:
        reviewers = {r.reviewer for r in basis}
        return len(reviewers) == 1 and planted[reviewers.pop()] < floor

    return hides


def _rate(basis: Sequence[ItemRecord], success: Callable, hides: Hides) -> Rate:
    return Rate.hidden() if hides(basis) else Rate.of([success(r) for r in basis])


def _caught(record: ItemRecord) -> bool:
    return record.outcome is Outcome.CATCH


def catch_rate(records: Iterable[ItemRecord], hides: Hides = nothing_hidden) -> Rate:
    """Planted errors caught out of planted errors decided on at first sight."""
    return _rate(scored(records), _caught, hides)


def by_defect_class(
    records: Sequence[ItemRecord], hides: Hides = nothing_hidden
) -> dict[DefectClass, Rate]:
    """The catch rate for each class that has been decided on."""
    counted = scored(records)
    by_class = {d: [r for r in counted if r.defect_class is d] for d in DefectClass}
    return {defect: _rate(basis, _caught, hides) for defect, basis in by_class.items() if basis}


def approvals(records: Iterable[ItemRecord]) -> list[ItemRecord]:
    return [r for r in records if r.decision is Decision.APPROVE]


def fast_approvals(
    records: Iterable[ItemRecord],
    min_seconds: Mapping[str, float],
    hides: Hides = nothing_hidden,
) -> Rate:
    """Approvals made faster than the queue's minimum review time, out of all approvals.
    Planted items count: this measures how the reviewer behaved.
    """
    return _rate(approvals(records), lambda r: r.dwell_ms < min_seconds[r.queue] * 1000, hides)


def median_dwell_ms(records: Iterable[ItemRecord]) -> float | None:
    """The middle time from shown to decided, over all decided items."""
    times = [r.dwell_ms for r in records if r.decided]
    return median(times) if times else None


def real_decisions(records: Iterable[ItemRecord]) -> list[ItemRecord]:
    return [r for r in records if r.decided and not r.planted]


def agreement_rate(records: Iterable[ItemRecord], hides: Hides = nothing_hidden) -> Rate:
    """Real AI proposals accepted unchanged, out of real proposals decided. Planted items are
    left out: they are not the AI's proposals.
    """
    return _rate(real_decisions(records), lambda r: r.decision is Decision.APPROVE, hides)


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


def fatigue(
    records: Sequence[ItemRecord],
    places: Mapping[str, Place],
    hides: Hides = nothing_hidden,
) -> Fatigue:
    counted = scored(records)
    by_position, by_hour = {}, {}
    for label, first, last in POSITION_BUCKETS:
        by_position[label] = [
            r
            for r in counted
            if first <= places[r.item_id].position
            and (last is None or places[r.item_id].position <= last)
        ]
    for number, label in enumerate(HOUR_BUCKETS, start=1):
        by_hour[label] = [
            r for r in counted if min(places[r.item_id].hour, len(HOUR_BUCKETS)) == number
        ]
    early = [r for r in counted if places[r.item_id].early]
    late = [r for r in counted if not places[r.item_id].early]
    return Fatigue(
        early=_rate(early, _caught, hides),
        late=_rate(late, _caught, hides),
        by_position={k: _rate(v, _caught, hides) for k, v in by_position.items() if v},
        by_hour={k: _rate(v, _caught, hides) for k, v in by_hour.items() if v},
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
    #: ``dwell_ms`` was held back to protect one reviewer, rather than there being none.
    dwell_withheld: bool = False


def summarise(
    records: Sequence[ItemRecord],
    places: Mapping[str, Place],
    min_seconds: Mapping[str, float],
    hides: Hides = nothing_hidden,
) -> Summary:
    """For a group, pass ``one_reviewer_rule``. Counts of work done are never withheld,
    because load explains results.
    """
    decided = [r for r in records if r.decided]
    dwell_withheld = hides(decided)
    return Summary(
        decided=len(decided),
        decided_real=sum(not r.planted for r in decided),
        catch=catch_rate(decided, hides),
        classes=by_defect_class(decided, hides),
        fast=fast_approvals(decided, min_seconds, hides),
        dwell_ms=None if dwell_withheld else median_dwell_ms(decided),
        dwell_withheld=dwell_withheld,
        agreement=agreement_rate(decided, hides),
        load=load(decided, places),
        fatigue=fatigue(decided, places, hides),
        repeats=Rate.of(
            [
                r.outcome is Outcome.CATCH
                for r in decided
                if r.planted and not r.first_sight and not r.voided
            ]
        ),
        pending_planted=sum(r.planted and not r.decided and bool(r.showings) for r in records),
    )
