"""What a report contains, before it is turned into HTML or CSV (step 2.7).

**Reviewer privacy.** By default a report is about the system: no reviewer is named. Individual
results are included only when asked for, are never calculated for fewer planted errors than the
queue's minimum (10 or more), and always sit next to that person's workload. A group's figure
that rests on a single reviewer *is* that person's figure, so it is held back under the same
rule: scores, and also how fast they approved and how often they agreed with the AI.

One limit: subtraction can work out a hidden figure, from a total and its other parts, or from
a group's figure and every other member's. Individual reports are for QA only, under a written
policy.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from reviewproof.config import Config, QueueConfig, Thresholds
from reviewproof.feedback import Outcome
from reviewproof.metrics.checks import Check, Flag, evaluate, reviewer_flags
from reviewproof.metrics.measures import (
    Hides,
    Place,
    Summary,
    as_recorded,
    catch_rate,
    left_out,
    one_reviewer_rule,
    scored,
    sessions,
    summarise,
)
from reviewproof.metrics.projection import ItemRecord, Projection, parse_time
from reviewproof.metrics.stats import Rate
from reviewproof.planted import PlantedLibrary
from reviewproof.store.eventlog import Head
from reviewproof.store.schema import StoreError, open_readonly


class ReportError(ValueError):
    """The report cannot be made as asked."""


@dataclass(frozen=True)
class Voided:
    """What voided planted errors take out of one set of figures, shown beside them so that a
    void never makes results look better without saying so (ADR-008). It names none.
    """

    #: Planted errors voided that had results in these figures, and the reason for each.
    planted_errors: int
    reasons: dict[str, int]
    #: Results left out of every score.
    results: int
    #: Of those, how many were catches. Withheld if it would describe one person.
    left_out: Rate
    #: The catch rate with them included. Withheld together with ``left_out``, because one
    #: would give away the other.
    with_them: Rate


@dataclass(frozen=True)
class Group:
    """Results for some set of items: a version of the AI system, or a site."""

    label: str
    #: Its figures say whether they were withheld to protect a single reviewer.
    summary: Summary
    voided: Voided | None = None

    @property
    def withheld(self) -> bool:
        """Whether the group's catch rate is withheld."""
        return self.summary.catch.withheld


@dataclass(frozen=True)
class ReviewerResult:
    reviewer: str
    decided: int
    planted: int
    days: int
    max_per_day: int
    #: None when there are too few planted errors to show anything about this person's work.
    summary: Summary | None
    flags: tuple[Flag, ...] = ()
    #: Its rates are withheld when too few results are left to show anything about them.
    voided: Voided | None = None
    #: What would be flagged with voided results included, if that differs from ``flags``.
    flags_with_voided: tuple[Flag, ...] | None = None


@dataclass(frozen=True)
class Programme:
    """The health of the planted-error programme itself."""

    usable_planted: int
    held_back: int
    shortfalls: int
    repeats: int
    pending: int
    #: Execution refused for a reason other than the item being planted, by reason.
    refusals: dict[str, int]
    failed_executions: int


@dataclass(frozen=True)
class QueueReport:
    name: str
    task: str
    site: str | None
    min_review_seconds: float
    planted_rate: float
    thresholds: Thresholds
    summary: Summary
    checks: tuple[Check, ...]
    programme: Programme
    #: None unless individual results were asked for.
    reviewers: tuple[ReviewerResult, ...] | None
    voided: Voided | None = None

    @property
    def withheld(self) -> bool:
        """Whether the queue's catch rate is withheld."""
        return self.summary.catch.withheld


@dataclass(frozen=True)
class Report:
    generated_at: datetime
    since: datetime | None
    queues: tuple[QueueReport, ...]
    by_version: tuple[Group, ...]
    by_site: tuple[Group, ...]
    head: Head
    #: Whether the log was also checked against a head exported earlier.
    anchored: bool = False


def build(
    projection: Projection,
    config: Config,
    planted_db: str | Path,
    queues: Sequence[str] | None = None,
    since: datetime | None = None,
    individuals: bool = False,
    now: datetime | None = None,
    anchored: bool = False,
) -> Report:
    in_log = sorted({r.queue for r in projection.records})
    names = list(queues) if queues else (in_log or sorted(config.queues))
    unknown = [name for name in names if name not in config.queues]
    if unknown:
        raise ReportError(f"queue(s) not in the settings: {', '.join(unknown)}")
    missing = [name for name in in_log if name not in config.queues]
    if missing:
        # Sessions are worked out across queues, so every queue in the log needs its settings.
        raise ReportError(f"the log has queue(s) the settings do not: {', '.join(missing)}")

    places = sessions(projection.records, session_gaps(config))
    minimum = minimum_review_times(config)
    chosen = [r for r in projection.records if r.queue in names and in_period(r, since)]

    with closing(open_readonly(planted_db, "planted")) as planted:
        sections = tuple(
            _queue(
                config.queues[name],
                config,
                [r for r in chosen if r.queue == name],
                places,
                minimum,
                projection,
                planted,
                since,
                individuals,
            )
            for name in names
        )

    floor = max(config.queues[n].thresholds.individual_min_planted for n in names)

    def group(label: str, records: list[ItemRecord]) -> Group:
        hides = one_reviewer_rule(records, floor)
        return Group(
            label,
            summarise(records, places, minimum, hides),
            voided_summary(records, hides),
        )

    def label_of(system_id: str) -> str:
        context = projection.contexts.get(system_id)
        return context.label if context else f"unregistered version {system_id}"

    versions = sorted({r.system_id for r in chosen}, key=label_of)
    by_version = tuple(
        group(label_of(v), [r for r in chosen if r.system_id == v]) for v in versions
    )

    def site_of(record: ItemRecord) -> str:
        return config.queues[record.queue].site or "(no site set)"

    by_site = tuple(
        group(site, [r for r in chosen if site_of(r) == site])
        for site in sorted({site_of(r) for r in chosen})
    )
    return Report(
        generated_at=now or datetime.now(timezone.utc),
        since=since,
        queues=sections,
        by_version=by_version,
        by_site=by_site,
        head=projection.head,
        anchored=anchored,
    )


def voided_summary(records: Sequence[ItemRecord], hides: Hides) -> Voided | None:
    """What voids leave out of the figures for ``records``, or None if they leave out nothing."""
    excluded = left_out(records)
    if not excluded:
        return None
    reason_of = {r.planted_error_id: r.voided for r in excluded}
    everything = as_recorded(records)
    # Beside the figure without them, either of these would give away the other.
    private = hides(scored(records)) or hides(excluded)
    caught = [r.outcome is Outcome.CATCH for r in excluded]
    return Voided(
        planted_errors=len(reason_of),
        reasons=dict(sorted(Counter(reason_of.values()).items())),
        results=len(excluded),
        left_out=Rate.hidden() if private else Rate.of(caught),
        with_them=Rate.hidden() if private else catch_rate(everything),
    )


def session_gaps(config: Config) -> dict[str, int]:
    return {n: q.thresholds.session_gap_minutes for n, q in config.queues.items()}


def minimum_review_times(config: Config) -> dict[str, float]:
    return {n: q.min_review_seconds for n, q in config.queues.items()}


def in_period(record: ItemRecord, since: datetime | None) -> bool:
    """A decided item belongs to the period it was decided in; an undecided one to the period
    it was submitted in.
    """
    if since is None:
        return True
    return (record.decided_at or record.submitted_at) >= since


def _queue(
    queue: QueueConfig,
    config: Config,
    records: list[ItemRecord],
    places: dict[str, Place],
    minimum: dict[str, float],
    projection: Projection,
    planted: sqlite3.Connection,
    since: datetime | None,
    individuals: bool,
) -> QueueReport:
    limits = queue.thresholds
    hides = one_reviewer_rule(records, limits.individual_min_planted)
    summary = summarise(records, places, minimum, hides)
    refusals = [
        r for r in projection.refusals if r.queue == queue.name and (not since or r.at >= since)
    ]
    checks = evaluate(records, places, minimum, limits, refusals)

    library = PlantedLibrary(planted, config)
    counts: dict[str, int] = {}
    for refusal in refusals:
        counts[refusal.reason] = counts.get(refusal.reason, 0) + 1
    programme = Programme(
        usable_planted=len(library.available(queue.name)),
        held_back=len(library.stale(queue.name)),
        shortfalls=_shortfalls(planted, queue.name, since),
        repeats=summary.repeats.total,
        pending=summary.pending_planted,
        refusals=counts,
        failed_executions=sum(r.execute_failed for r in records),
    )

    reviewers = None
    if individuals:
        reviewers = tuple(
            _reviewer(name, [r for r in records if r.reviewer == name], places, minimum, limits)
            for name in sorted({r.reviewer for r in records if r.decided})
        )
    return QueueReport(
        name=queue.name,
        task=queue.task,
        site=queue.site,
        min_review_seconds=queue.min_review_seconds,
        planted_rate=queue.planted_rate,
        thresholds=limits,
        summary=summary,
        checks=tuple(checks),
        programme=programme,
        reviewers=reviewers,
        voided=voided_summary(records, hides),
    )


def _reviewer(
    name: str,
    records: list[ItemRecord],
    places: dict[str, Place],
    minimum: dict[str, float],
    limits: Thresholds,
) -> ReviewerResult:
    summary = summarise(records, places, minimum)
    flags = reviewer_flags(records, minimum, limits)
    # With too few results left to show anything about this person, what was left out is
    # withheld too.
    voided = voided_summary(records, lambda basis: flags is None)
    with_voided = None
    if voided is not None and flags is not None:
        with_voided = tuple(reviewer_flags(as_recorded(records), minimum, limits) or ())
        if with_voided == tuple(flags):
            with_voided = None
    return ReviewerResult(
        reviewer=name,
        decided=summary.decided,
        planted=len(scored(records)),
        days=summary.load.reviewer_days,
        max_per_day=summary.load.max_per_day,
        summary=None if flags is None else summary,
        flags=tuple(flags or ()),
        voided=voided,
        flags_with_voided=with_voided,
    )


def _shortfalls(planted: sqlite3.Connection, queue: str, since: datetime | None) -> int:
    try:
        rows = planted.execute(
            "SELECT ts_utc FROM gate_shortfalls WHERE queue_id = ?", (queue,)
        ).fetchall()
    except sqlite3.Error as error:
        raise StoreError(f"cannot read the gate's state: {error}") from error
    return sum(since is None or parse_time(row[0]) >= since for row in rows)
