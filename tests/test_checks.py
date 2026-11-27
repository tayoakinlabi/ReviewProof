"""Thresholds, exactly at their limits (step 2.5), and sessions (step 2.4)."""

from dataclasses import replace
from datetime import timedelta

import pytest
from records_factory import MONDAY, QUEUE, planted_run, record

from reviewproof.config import Thresholds
from reviewproof.metrics.checks import (
    State,
    check_agreement,
    check_catch_rate,
    check_classes,
    check_fast_approvals,
    check_fatigue,
    check_load,
    check_safety,
    evaluate,
    reviewer_flags,
)
from reviewproof.metrics.measures import fatigue, one_reviewer_rule, sessions, summarise
from reviewproof.metrics.projection import Refusal
from reviewproof.models import Decision, DefectClass

LIMITS = Thresholds()
MINIMUM = {QUEUE: 30}


def places(records, gap=30):
    return sessions(records, {QUEUE: gap})


# ----------------------------------------------------------------- catch rate


@pytest.mark.parametrize(
    ("outcomes", "state"),
    [
        ([False] * 9, State.NOT_ENOUGH_DATA),
        ([True] * 8 + [False] * 2, State.OK),  # exactly 80% is not below 80%
        ([True] * 7 + [False] * 3, State.BREACH),
        ([True] * 10, State.OK),
    ],
)
def test_catch_rate_limit(outcomes, state):
    assert check_catch_rate(planted_run(outcomes), LIMITS).state is state


def test_the_catch_rate_rolls_over_the_latest_ones():
    old_misses = planted_run([False] * 40)
    recent_catches = planted_run([True] * 50)
    assert check_catch_rate(old_misses + recent_catches, LIMITS).state is State.OK
    assert check_catch_rate(recent_catches + planted_run([False] * 40), LIMITS).state is (
        State.BREACH
    )


@pytest.mark.req("REQ-036")
def test_repeats_do_not_count():
    records = planted_run([True] * 10) + planted_run([False] * 10, first_sight=False)
    check = check_catch_rate(records, LIMITS)
    assert check.state is State.OK
    assert "10 were caught" in check.detail


# ----------------------------------------------------------------- classes


def test_each_weak_class_is_named_in_one_alert():
    records = planted_run([False] * 10, defect=DefectClass.OMISSION)
    records += planted_run([True] * 5 + [False] * 5, defect=DefectClass.WRONG_VALUE)
    records += planted_run([True] * 6 + [False] * 4, defect=DefectClass.POLICY_BREACH)
    records += planted_run([False] * 9, defect=DefectClass.WRONG_TARGET)
    check = check_classes(records, LIMITS)
    assert check.state is State.BREACH
    assert "omission: 0 of 10" in check.detail
    assert "wrong value: 5 of 10" in check.detail
    assert "policy breach" not in check.detail  # exactly 60% is not below 60%
    assert "wrong target" not in check.detail  # only 9: not judged


@pytest.mark.req("REQ-039")
def test_classes_without_enough_data_are_not_judged():
    assert check_classes(planted_run([False] * 9), LIMITS).state is State.NOT_ENOUGH_DATA
    assert check_classes(planted_run([True] * 10), LIMITS).state is State.OK


# ----------------------------------------------------------------- fast approvals


def approvals(fast, slow, week=0):
    base = MONDAY + timedelta(weeks=week)
    return [
        record(at=base + timedelta(minutes=n), dwell=10 if n < fast else 60)
        for n in range(fast + slow)
    ]


@pytest.mark.req("REQ-038")
@pytest.mark.parametrize(
    ("fast", "slow", "state"),
    [
        (9, 0, State.NOT_ENOUGH_DATA),
        (2, 8, State.OK),  # exactly 20%
        (3, 7, State.BREACH),
    ],
)
def test_weekly_fast_approval_limit(fast, slow, state):
    assert check_fast_approvals(approvals(fast, slow), MINIMUM, LIMITS).state is state


def test_weeks_are_judged_separately_and_reported_together():
    records = approvals(0, 100) + approvals(5, 5, week=1) + approvals(8, 2, week=2)
    check = check_fast_approvals(records, MINIMUM, LIMITS)
    assert check.state is State.BREACH
    assert "week 11 of 2026: 50%" in check.detail and "week 12 of 2026: 80%" in check.detail
    assert "week 10" not in check.detail


@pytest.mark.req("REQ-038")
def test_exactly_the_minimum_time_is_not_fast():
    records = [record(dwell=30) for _ in range(10)]
    assert check_fast_approvals(records, MINIMUM, LIMITS).state is State.OK


@pytest.mark.req("REQ-038")
def test_rejections_are_not_fast_approvals():
    records = [record(dwell=1, decision=Decision.REJECT) for _ in range(20)]
    assert check_fast_approvals(records, MINIMUM, LIMITS).state is State.NOT_ENOUGH_DATA


# ----------------------------------------------------------------- agreement


def agreeing(real, disagreements=0):
    return [record() for _ in range(real)] + [
        record(decision=Decision.REJECT) for _ in range(disagreements)
    ]


def test_high_agreement_alone_does_not_alert():
    steady = planted_run([True, False] * 10) + agreeing(100)
    assert check_agreement(steady, LIMITS).state is State.OK


def test_high_agreement_with_a_falling_catch_rate_alerts():
    falling = planted_run([True] * 10 + [False] * 10) + agreeing(100)
    check = check_agreement(falling, LIMITS)
    assert check.state is State.BREACH
    assert "from 100% to 0%" in check.detail


def test_a_falling_catch_rate_with_ordinary_agreement_does_not_alert():
    records = planted_run([True] * 10 + [False] * 10) + agreeing(98, 2)
    assert check_agreement(records, LIMITS).state is State.OK  # exactly 98%


@pytest.mark.parametrize(("planted", "real"), [(19, 100), (20, 9)])
def test_a_trend_needs_enough_of_both(planted, real):
    records = planted_run([True] * planted) + agreeing(real)
    assert check_agreement(records, LIMITS).state is State.NOT_ENOUGH_DATA


# ----------------------------------------------------------------- fatigue and sessions


def one_session(outcomes):
    return planted_run(outcomes)


@pytest.mark.req("REQ-038")
@pytest.mark.parametrize(
    ("late_catches", "state"), [(10, State.OK), (9, State.OK), (8, State.BREACH)]
)
def test_fatigue_alerts_at_a_20_point_drop(late_catches, state):
    outcomes = [True] * 10 + [True] * late_catches + [False] * (10 - late_catches)
    records = one_session(outcomes)
    check = check_fatigue(records, places(records), LIMITS)
    assert check.state is state


def test_fatigue_needs_enough_on_both_sides():
    records = one_session([True] * 18)
    assert check_fatigue(records, places(records), LIMITS).state is State.NOT_ENOUGH_DATA
    assert fatigue([], {}).drop is None


@pytest.mark.req("REQ-038")
@pytest.mark.parametrize(
    ("size", "early"),
    [(1, [True]), (2, [True, False]), (5, [True, True, True, False, False])],
)
def test_the_middle_item_of_an_odd_session_counts_as_early(size, early):
    records = [record(minute=n) for n in range(size)]
    found = places(records)
    assert [found[r.item_id].early for r in records] == early


@pytest.mark.req("REQ-038")
def test_a_long_pause_starts_a_new_session():
    records = [record(minute=0), record(minute=30), record(minute=61), record(minute=62)]
    found = places(records)
    assert [found[r.item_id].session for r in records] == [1, 1, 2, 2]
    assert [found[r.item_id].position for r in records] == [1, 2, 1, 2]


@pytest.mark.req("REQ-038")
def test_sessions_are_per_person_and_cross_queues():
    records = [
        record(minute=0, reviewer="a"),
        record(minute=1, reviewer="b"),
        record(minute=2, reviewer="a", queue="other"),
    ]
    found = sessions(records, {QUEUE: 30, "other": 30})
    assert [found[r.item_id].position for r in records] == [1, 1, 2]


# ----------------------------------------------------------------- load and safety


def test_load_is_checked_only_when_capped():
    records = [record() for _ in range(5)]
    assert check_load(records, LIMITS) is None
    capped = replace(LIMITS, max_items_per_reviewer_per_day=5)
    assert check_load(records, capped).state is State.OK
    over = check_load(records, replace(LIMITS, max_items_per_reviewer_per_day=4))
    assert over.state is State.BREACH and "most: 5" in over.detail


@pytest.mark.parametrize(
    ("reasons", "state"),
    [
        ([], State.OK),
        (["payload_changed"], State.OK),  # the guard did its job; shown in the report
        (["check_failed"], State.BREACH),
        (["reserved_target", "check_failed"], State.BREACH),
    ],
)
def test_safety_stops_alert(reasons, state):
    refusals = [Refusal(QUEUE, MONDAY, reason) for reason in reasons]
    assert check_safety(refusals).state is state


@pytest.mark.req("REQ-040")
def test_evaluate_runs_every_rule_and_names_no_one():
    records = planted_run([False] * 12, reviewer="SECRET-REVIEWER") + agreeing(20)
    checks = evaluate(records, places(records), MINIMUM, LIMITS)
    assert {c.rule for c in checks} == {
        "catch_rate",
        "defect_class",
        "fast_approvals",
        "agreement",
        "fatigue",
        "safety",
    }
    text = " ".join(c.detail for c in checks)
    assert "SECRET-REVIEWER" not in text and "PE-" not in text and "item-" not in text


@pytest.mark.req("REQ-039")
def test_no_data_is_never_a_pass():
    checks = evaluate([], {}, MINIMUM, LIMITS)
    states = {c.rule: c.state for c in checks}
    assert all(state is State.NOT_ENOUGH_DATA for rule, state in states.items() if rule != "safety")
    summary = summarise([], {}, MINIMUM)
    assert summary.catch.value is None and summary.dwell_ms is None


# ----------------------------------------------------------------- one person's figures
#
# A group's figure that rests on one reviewer with fewer than ten planted errors is that
# person's figure, whichever figure it is and however the group is broken down.


def rule(records):
    return one_reviewer_rule(records, LIMITS.individual_min_planted)


def by_rule(records, limits=LIMITS):
    return {c.rule: c for c in evaluate(records, places(records), MINIMUM, limits)}


def summary_of(records):
    return summarise(records, places(records), MINIMUM, rule(records))


def two_people(count, **arguments):
    return [record(reviewer=f"r-{n % 2}", **arguments) for n in range(count)]


def one_seen_by_two():
    return planted_run([True] * 12, reviewer="r-1") + planted_run(
        [False] * 3, reviewer="r-2", defect=DefectClass.WRONG_VALUE
    )


@pytest.mark.req("REQ-040")
def test_a_lone_reviewers_fast_approvals_raise_no_alert():
    records = [record(dwell=1) for _ in range(12)]
    check = by_rule(records)["fast_approvals"]
    assert check.state is State.NOT_ENOUGH_DATA and "Withheld" in check.detail
    assert "%" not in check.detail
    assert summary_of(records).fast.withheld


@pytest.mark.req("REQ-040")
def test_a_lone_reviewer_with_enough_planted_errors_is_shown():
    records = planted_run([True] * 10) + [record(dwell=1) for _ in range(12)]
    assert by_rule(records)["fast_approvals"].state is State.BREACH
    summary = summary_of(records)
    assert not summary.fast.withheld and not summary.catch.withheld
    assert summary.dwell_ms is not None and not summary.dwell_withheld


@pytest.mark.req("REQ-040")
def test_approvals_that_are_all_one_persons_are_withheld():
    # Two people decide, but only one ever approves.
    records = [record(reviewer="r-1", dwell=1) for _ in range(12)] + [
        record(reviewer="r-2", decision=Decision.REJECT) for _ in range(12)
    ]
    check = by_rule(records)["fast_approvals"]
    assert check.state is State.NOT_ENOUGH_DATA and "Withheld" in check.detail
    summary = summary_of(records)
    assert summary.fast.withheld
    # Both contributed to these.
    assert not summary.agreement.withheld and summary.dwell_ms is not None


@pytest.mark.req("REQ-040")
@pytest.mark.parametrize(
    ("shared_week_dwell", "state"), [(1, State.BREACH), (60, State.NOT_ENOUGH_DATA)]
)
def test_a_week_that_is_one_persons_is_not_described(shared_week_dwell, state):
    shared = two_people(20, dwell=shared_week_dwell)
    alone = [
        record(reviewer="r-9", dwell=1, at=MONDAY + timedelta(weeks=1, minutes=n))
        for n in range(12)
    ]
    check = by_rule(shared + alone)["fast_approvals"]
    assert "week 11" not in check.detail
    # A breach two people share is still reported. Without one, the hidden week means the
    # queue cannot be called within its limit.
    assert check.state is state
    assert ("week 10" if state is State.BREACH else "Withheld") in check.detail


@pytest.mark.req("REQ-040")
def test_a_breakdown_that_is_one_persons_is_withheld():
    summary = summary_of(one_seen_by_two())
    assert not summary.catch.withheld and summary.catch.total == 15
    assert not summary.classes[DefectClass.OMISSION].withheld
    hidden = summary.classes[DefectClass.WRONG_VALUE]
    assert hidden.withheld and (hidden.successes, hidden.total, hidden.value) == (0, 0, None)


@pytest.mark.req("REQ-040")
def test_a_kind_of_error_judged_from_one_person_is_not_described():
    # Possible only when the settings judge a kind from fewer examples than the minimum.
    limits = replace(LIMITS, min_sample=3)
    check = by_rule(one_seen_by_two(), limits)["defect_class"]
    assert check.state is State.NOT_ENOUGH_DATA and "Withheld" in check.detail
    assert "wrong value" not in check.detail


@pytest.mark.req("REQ-040")
def test_every_part_of_a_lone_reviewers_session_figures_is_withheld():
    result = summary_of(planted_run([True] * 3)).fatigue  # two early in the session, one late
    parts = [result.early, result.late, *result.by_position.values(), *result.by_hour.values()]
    assert result.by_position and result.by_hour
    assert all(part.withheld and part.total == 0 for part in parts)


@pytest.mark.req("REQ-040")
def test_a_place_in_the_session_that_only_one_person_reached_is_withheld():
    first = planted_run([True] * 10, reviewer="r-1")  # positions 1 to 10
    later = [record(reviewer="r-2", minute=500 + n) for n in range(11)] + [
        record(planted=True, reviewer="r-2", minute=511 + n) for n in range(3)
    ]  # r-2's planted errors come 12th to 14th, a place r-1 never reached
    summary = summary_of(first + later)
    assert not summary.catch.withheld
    assert summary.fatigue.by_position["11-25"].withheld
    assert not summary.fatigue.by_position["1-10"].withheld


@pytest.mark.req("REQ-040")
@pytest.mark.parametrize("rule_name", ["catch_rate", "agreement"])
def test_a_window_or_half_that_is_one_persons_is_not_judged(rule_name):
    # With a higher minimum, each half is one person's while the whole is two people's.
    limits = replace(LIMITS, individual_min_planted=20, rolling_window=10)
    records = (
        planted_run([True] * 10, reviewer="r-1")
        + planted_run([False] * 10, reviewer="r-2")
        + two_people(100)
    )
    check = by_rule(records, limits)[rule_name]
    assert check.state is State.NOT_ENOUGH_DATA and "Withheld" in check.detail


@pytest.mark.req("REQ-040")
@pytest.mark.parametrize("alone_in", ["early", "late"])
def test_fatigue_is_not_judged_when_a_half_is_one_persons(alone_in):
    # In sessions of two, r-1 has a planted error in both halves and r-2 in one of them.
    first, second = planted_run([True, True], reviewer="r-1")
    theirs = [record(reviewer="r-2", minute=900), record(reviewer="r-2", minute=901)]
    slot = 1 if alone_in == "early" else 0
    theirs[slot] = record(planted=True, reviewer="r-2", minute=900 + slot)
    check = by_rule([first, second, *theirs])["fatigue"]
    assert check.state is State.NOT_ENOUGH_DATA and "Withheld" in check.detail


# ----------------------------------------------------------------- individual flags


def flags(records, limits=LIMITS):
    found = reviewer_flags(records, MINIMUM, limits)
    return None if found is None else [f.kind for f in found]


def test_nothing_is_said_about_a_person_below_ten_planted_errors():
    assert flags(planted_run([False] * 9) + [record(dwell=1) for _ in range(50)]) is None


def test_a_careful_person_is_not_flagged():
    assert flags(planted_run([True] * 9 + [False])) == []


def test_a_shortfall_that_could_be_chance_says_so():
    assert flags(planted_run([True] * 7 + [False] * 3)) == ["catch_below"]


def test_a_clear_shortfall_says_so():
    assert flags(planted_run([True] * 3 + [False] * 17)) == ["catch_clearly_below"]


def test_fast_approving_is_flagged():
    records = planted_run([True] * 10) + [record(dwell=5) for _ in range(20)]
    assert flags(records) == ["fast_approvals"]


def test_the_minimum_can_be_raised():
    assert flags(planted_run([False] * 15), replace(LIMITS, individual_min_planted=20)) is None
