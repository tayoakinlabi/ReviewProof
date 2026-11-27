"""Thresholds (step 2.5): which metrics need attention.

Every rule has three possible states. ``not_enough_data`` is not a pass: a report must never
present too few planted errors as reassurance.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from reviewproof.config import Thresholds
from reviewproof.feedback import Outcome
from reviewproof.metrics.measures import (
    Place,
    agreement_rate,
    by_defect_class,
    fast_approvals,
    fatigue,
    scored,
)
from reviewproof.metrics.projection import ItemRecord, Refusal
from reviewproof.metrics.stats import Rate

CLASS_NAMES = {
    "wrong_value": "wrong value",
    "omission": "omission",
    "wrong_classification": "wrong classification",
    "unsupported_claim": "unsupported claim",
    "wrong_target": "wrong target",
    "policy_breach": "policy breach",
}


class State(str, Enum):
    BREACH = "breach"
    OK = "ok"
    NOT_ENOUGH_DATA = "not_enough_data"


@dataclass(frozen=True)
class Check:
    rule: str
    title: str
    state: State
    #: Plain language, with the numbers. Names no reviewer, item or planted error.
    detail: str


def percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def check_catch_rate(records: Sequence[ItemRecord], limits: Thresholds) -> Check:
    """The rolling catch rate over the latest planted errors."""
    latest = scored(records)[-limits.rolling_window :]
    rate = Rate.of([r.outcome is Outcome.CATCH for r in latest])
    title = "Planted errors caught"
    if rate.total < limits.min_sample:
        return Check(
            "catch_rate",
            title,
            State.NOT_ENOUGH_DATA,
            f"Only {rate.total} planted errors so far; at least {limits.min_sample} are needed.",
        )
    detail = (
        f"Of the latest {rate.total} planted errors, {rate.successes} were caught "
        f"({percent(rate.value)}). The target is at least {percent(limits.catch_rate_min)}."
    )
    state = State.BREACH if rate.value < limits.catch_rate_min else State.OK
    return Check("catch_rate", title, state, detail)


def check_classes(records: Sequence[ItemRecord], limits: Thresholds) -> Check:
    """Any defect class that slips through. One alert names all of them."""
    title = "Kinds of error"
    judged = {
        defect: rate
        for defect, rate in by_defect_class(records).items()
        if rate.total >= limits.min_sample
    }
    if not judged:
        return Check(
            "defect_class",
            title,
            State.NOT_ENOUGH_DATA,
            f"No kind of error has {limits.min_sample} planted errors yet.",
        )
    weak = [
        f"{CLASS_NAMES[defect.value]}: {rate.successes} of {rate.total} caught "
        f"({percent(rate.value)})"
        for defect, rate in judged.items()
        if rate.value < limits.class_catch_rate_min
    ]
    if weak:
        return Check(
            "defect_class",
            title,
            State.BREACH,
            f"Below {percent(limits.class_catch_rate_min)}: {'; '.join(weak)}.",
        )
    return Check(
        "defect_class",
        title,
        State.OK,
        f"Every kind with at least {limits.min_sample} planted errors is caught at least "
        f"{percent(limits.class_catch_rate_min)} of the time ({len(judged)} judged).",
    )


def check_fast_approvals(
    records: Sequence[ItemRecord], min_seconds: Mapping[str, float], limits: Thresholds
) -> Check:
    """Judged week by week (ISO weeks, UTC). One alert names every week over the limit."""
    title = "Very fast approvals"
    weeks: dict[tuple[int, int], list[ItemRecord]] = defaultdict(list)
    for record in records:
        if record.decided:
            year, week, _ = record.decided_at.isocalendar()
            weeks[year, week].append(record)
    judged = {}
    for week, decided in sorted(weeks.items()):
        rate = fast_approvals(decided, min_seconds)
        if rate.total >= limits.min_sample:
            judged[week] = rate
    if not judged:
        return Check(
            "fast_approvals",
            title,
            State.NOT_ENOUGH_DATA,
            f"No week has {limits.min_sample} approvals yet.",
        )
    over = [
        f"week {week} of {year}: {percent(rate.value)} of {rate.total} approvals"
        for (year, week), rate in judged.items()
        if rate.value > limits.fast_approval_share_max
    ]
    limit = percent(limits.fast_approval_share_max)
    if over:
        return Check(
            "fast_approvals",
            title,
            State.BREACH,
            f"More than {limit} were faster than the minimum review time in {'; '.join(over)}.",
        )
    return Check(
        "fast_approvals",
        title,
        State.OK,
        f"No week had more than {limit} of approvals faster than the minimum review time.",
    )


def check_agreement(records: Sequence[ItemRecord], limits: Thresholds) -> Check:
    """Near-total agreement while catching less: a sign of approving on habit.

    "Falling" compares the later half of the planted errors with the earlier half.
    """
    title = "Agreement with the AI"
    agreement = agreement_rate(records)
    counted = scored(records)
    half = len(counted) // 2
    if agreement.total < limits.min_sample or half < limits.min_sample:
        return Check(
            "agreement",
            title,
            State.NOT_ENOUGH_DATA,
            f"Judging a trend needs {2 * limits.min_sample} planted errors and "
            f"{limits.min_sample} real items; there are {len(counted)} and {agreement.total}.",
        )
    earlier = Rate.of([r.outcome is Outcome.CATCH for r in counted[:half]])
    later = Rate.of([r.outcome is Outcome.CATCH for r in counted[half:]])
    falling = later.value < earlier.value
    detail = (
        f"{percent(agreement.value)} of real proposals were accepted unchanged, and the catch "
        f"rate went from {percent(earlier.value)} to {percent(later.value)}."
    )
    breach = agreement.value > limits.agreement_rate_max and falling
    return Check("agreement", title, State.BREACH if breach else State.OK, detail)


def check_fatigue(
    records: Sequence[ItemRecord], places: Mapping[str, Place], limits: Thresholds
) -> Check:
    title = "Attention late in sessions"
    result = fatigue(records, places)
    if min(result.early.total, result.late.total) < limits.min_sample:
        return Check(
            "fatigue",
            title,
            State.NOT_ENOUGH_DATA,
            f"At least {limits.min_sample} planted errors are needed both early and late in "
            f"sessions; there are {result.early.total} and {result.late.total}.",
        )
    detail = (
        f"Catch rate {percent(result.early.value)} in the first half of sessions and "
        f"{percent(result.late.value)} in the second half."
    )
    # A hair of tolerance, so that a drop of exactly the limit is not lost to rounding.
    breach = result.drop >= limits.fatigue_drop_max - 1e-9
    return Check("fatigue", title, State.BREACH if breach else State.OK, detail)


def check_load(records: Sequence[ItemRecord], limits: Thresholds) -> Check | None:
    """Only when a cap is set."""
    cap = limits.max_items_per_reviewer_per_day
    if cap is None:
        return None
    per_day: dict[tuple, int] = defaultdict(int)
    for record in records:
        if record.decided:
            per_day[record.reviewer, record.decided_at.date()] += 1
    over = sum(count > cap for count in per_day.values())
    if over:
        return Check(
            "load",
            "Daily load",
            State.BREACH,
            f"{over} time(s) a reviewer decided more than the cap of {cap} items in a day "
            f"(most: {max(per_day.values())}).",
        )
    return Check(
        "load", "Daily load", State.OK, f"No reviewer decided more than {cap} items in a day."
    )


def check_safety(refusals: Sequence[Refusal]) -> Check:
    """The guard stopping a reserved target ID means ReviewProof itself, or its data, is
    wrong. Failed checks mean something prevented execution. Both need a person.
    """
    serious = [r for r in refusals if r.reason in ("reserved_target", "check_failed")]
    if serious:
        reserved = sum(r.reason == "reserved_target" for r in serious)
        return Check(
            "safety",
            "Execution safety checks",
            State.BREACH,
            f"A safety check stopped execution {len(serious)} time(s), {reserved} of them "
            "because of a reserved target ID. Run rp verify and investigate.",
        )
    return Check(
        "safety", "Execution safety checks", State.OK, "No safety check had to stop execution."
    )


def evaluate(
    records: Sequence[ItemRecord],
    places: Mapping[str, Place],
    min_seconds: Mapping[str, float],
    limits: Thresholds,
    refusals: Sequence[Refusal] = (),
) -> list[Check]:
    """Every rule for one queue."""
    checks = [
        check_catch_rate(records, limits),
        check_classes(records, limits),
        check_fast_approvals(records, min_seconds, limits),
        check_agreement(records, limits),
        check_fatigue(records, places, limits),
        check_load(records, limits),
        check_safety(refusals),
    ]
    return [check for check in checks if check is not None]


@dataclass(frozen=True)
class Flag:
    """Something about one reviewer for QA to look into. Never sent as an alert."""

    kind: str
    text: str


def reviewer_flags(
    records: Sequence[ItemRecord], min_seconds: Mapping[str, float], limits: Thresholds
) -> list[Flag] | None:
    """Flags for one reviewer's items, or None with too few planted errors to say anything.

    A catch rate below target is graded by its 95% interval. With small samples a careful
    reviewer can fall below the target by chance, and the wording has to say so.
    """
    counted = scored(records)
    if len(counted) < limits.individual_min_planted:
        return None
    flags = []
    catch = Rate.of([r.outcome is Outcome.CATCH for r in counted])
    if catch.value < limits.catch_rate_min:
        if catch.interval[1] < limits.catch_rate_min:
            flags.append(Flag("catch_clearly_below", "Catch rate clearly below target"))
        else:
            flags.append(Flag("catch_below", "Catch rate below target, but this could be chance"))
    fast = fast_approvals(records, min_seconds)
    if fast.total >= limits.min_sample and fast.value > limits.fast_approval_share_max:
        flags.append(Flag("fast_approvals", "Many approvals faster than the minimum time"))
    return flags
