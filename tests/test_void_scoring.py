"""Voided results in scores, the "Voided results" check, and reviewer privacy (ADR-008).

The rule behind most of these: voiding must never make results look better without saying so.
"""

from dataclasses import replace

import pytest
from records_factory import QUEUE, planted_run, record

from reviewproof.config import ConfigError, Thresholds, parse_config
from reviewproof.metrics.checks import State, check_voided, evaluate, reviewer_flags
from reviewproof.metrics.measures import (
    as_recorded,
    left_out,
    nothing_hidden,
    one_reviewer_rule,
    scored,
    sessions,
    summarise,
)
from reviewproof.models import Decision, DefectClass
from reviewproof.report.model import voided_summary

LIMITS = Thresholds()
MINIMUM = {QUEUE: 30}


def voided(records, reason="not_defective"):
    return [replace(r, voided=reason) for r in records]


def places(records):
    return sessions(records, {QUEUE: 30})


def judge(records, limits=LIMITS, hides=nothing_hidden):
    return check_voided(records, places(records), limits, hides)


def real(count, **arguments):
    return [record(**arguments) for _ in range(count)]


# ----------------------------------------------------------------- what is left out


@pytest.mark.req("REQ-056")
def test_voided_results_leave_every_score_and_stay_in_work_done():
    kept = planted_run([True] * 6 + [False] * 2)
    gone = voided(planted_run([False] * 3, defect=DefectClass.WRONG_VALUE, dwell=1))
    repeat = voided(planted_run([True], first_sight=False))
    records = kept + gone + repeat + real(4)
    summary = summarise(records, places(records), MINIMUM)

    assert (summary.catch.successes, summary.catch.total) == (6, 8)
    assert DefectClass.WRONG_VALUE not in summary.classes
    assert summary.fatigue.early.total + summary.fatigue.late.total == 8
    assert sum(r.total for r in summary.fatigue.by_position.values()) == 8
    assert summary.repeats.total == 0
    # The reviewer did the work: it still counts towards load, speed and approvals.
    assert summary.decided == len(records) == summary.load.max_per_day
    assert summary.fast.total == sum(r.decision is Decision.APPROVE for r in records)
    assert summary.fast.successes == 3
    assert len(left_out(records)) == 3 and len(scored(as_recorded(records))) == 11


@pytest.mark.req("REQ-056")
def test_voided_results_do_not_count_towards_the_individual_minimum():
    records = planted_run([False] * 9) + voided(planted_run([False] * 5))
    assert reviewer_flags(records, MINIMUM, LIMITS) is None
    assert reviewer_flags(as_recorded(records), MINIMUM, LIMITS) is not None


# ----------------------------------------------------------------- the check


@pytest.mark.req("REQ-057")
def test_nothing_is_said_when_nothing_is_left_out():
    records = planted_run([False] * 20)
    assert judge(records) is None
    assert "voided" not in {c.rule for c in evaluate(records, places(records), MINIMUM, LIMITS)}
    # A voided planted error whose only copy was seen again is in no score either way.
    assert judge(records + voided(planted_run([True], first_sight=False))) is None


@pytest.mark.req("REQ-057")
def test_a_breach_that_voiding_removes_is_a_breach():
    # ADR-008's example: 8 catches in 11 is a breach; without a voided miss, 8 in 10 is not.
    records = planted_run([True] * 8 + [False] * 2) + voided(planted_run([False]))
    check = judge(records)
    assert check.state is State.BREACH
    assert "without them they are not: Planted errors caught." in check.detail
    assert "item-" not in check.detail and "PE-" not in check.detail


@pytest.mark.req("REQ-057")
def test_too_little_left_to_judge_counts_as_removing_a_breach():
    # Two people share the work, and two met what was voided, so nothing is one person's.
    records = planted_run([True] * 7, reviewer="r-1") + planted_run([False] * 2, reviewer="r-2")
    records += voided(planted_run([False], reviewer="r-1"))
    records += voided(planted_run([True], reviewer="r-2"))
    by_rule = {c.rule: c for c in evaluate(records, places(records), MINIMUM, LIMITS)}
    assert by_rule["catch_rate"].state is State.NOT_ENOUGH_DATA
    assert by_rule["voided"].state is State.BREACH
    assert "Planted errors caught" in by_rule["voided"].detail


@pytest.mark.req("REQ-057")
def test_voiding_that_hides_no_breach_is_within_limits():
    # One miss early and one late in the session, so no check is near its limit.
    outcomes = [True] * 2 + [False] + [True] * 11 + [False] + [True] * 5
    records = voided(planted_run([True])) + planted_run(outcomes)
    check = judge(records)
    assert check.state is State.OK and "hides no breach" in check.detail


@pytest.mark.req("REQ-057")
def test_one_kind_still_in_breach_does_not_cover_for_another():
    missed = planted_run([False] * 10)  # omissions: below the limit with or without
    values = planted_run([True] * 6 + [False] * 4, defect=DefectClass.WRONG_VALUE)
    gone = voided(planted_run([False] * 2, defect=DefectClass.WRONG_VALUE))
    check = judge(missed + values + gone)
    assert check.state is State.BREACH
    assert check.detail.endswith("they are not: Kinds of error (wrong value).")


@pytest.mark.req("REQ-057")
def test_a_hidden_fall_in_agreement_with_catching_less_is_a_breach():
    records = planted_run([True] * 20) + voided(planted_run([False])) + real(100)
    check = judge(records)
    assert check.state is State.BREACH and "Agreement with the AI" in check.detail


@pytest.mark.req("REQ-057")
def test_hidden_fatigue_is_a_breach():
    # One session of 25: the first 13 are early. Three late misses, one of them voided.
    outcomes = [True] * 22 + [False] * 3
    records = planted_run(outcomes)
    records[-1] = replace(records[-1], voided="not_findable")
    check = judge(records)
    assert check.state is State.BREACH
    assert check.detail.endswith("they are not: Attention late in sessions.")


@pytest.mark.req("REQ-057")
@pytest.mark.parametrize(
    ("kept", "gone", "state"),
    [
        (18, 2, State.OK),  # exactly 10% is not more than 10%
        (17, 3, State.BREACH),
        (6, 3, State.OK),  # fewer than 10 results: the share is not judged
        (6, 4, State.BREACH),
    ],
)
def test_too_large_a_share_voided_is_a_breach(kept, gone, state):
    records = planted_run([True] * kept) + voided(planted_run([True] * gone))
    check = judge(records)
    assert check.state is state
    if state is State.BREACH:
        assert f"{gone} of {kept + gone} planted-error results are voided" in check.detail


@pytest.mark.req("REQ-057")
def test_the_share_limit_comes_from_the_settings():
    records = planted_run([True] * 17) + voided(planted_run([True] * 3))
    loose = replace(LIMITS, voided_share_max=0.2)
    assert judge(records, loose).state is State.OK
    data = {
        "systems": [],
        "queues": [],
        "reporting": {"voided_share_max": 2},
    }
    with pytest.raises(ConfigError, match="voided_share_max must be a share"):
        parse_config(data, base_dir=None)


# ----------------------------------------------------------------- privacy


@pytest.mark.req("REQ-057")
@pytest.mark.req("REQ-040")
def test_for_one_reviewer_with_too_few_the_check_is_withheld_not_passed():
    records = planted_run([True] * 4) + voided(planted_run([False]))
    check = judge(records, hides=one_reviewer_rule(records, 10))
    assert check.state is State.NOT_ENOUGH_DATA and check.withheld
    assert "%" not in check.detail


@pytest.mark.req("REQ-057")
@pytest.mark.req("REQ-040")
def test_a_share_breach_is_still_said_because_it_is_only_counts():
    records = planted_run([True] * 6) + voided(planted_run([False] * 4))
    check = judge(records, hides=one_reviewer_rule(records, 10))
    assert check.state is State.BREACH
    assert "4 of 10 planted-error results are voided" in check.detail
    assert "they are not" not in check.detail  # which would say how they were decided


def one_persons_voids():
    """Two people share the scores; only r-2, with no results left, met what was voided."""
    shared = planted_run([True] * 12, reviewer="r-1") + planted_run([True] * 12, reviewer="r-3")
    return shared + voided(planted_run([False] * 2, reviewer="r-2"))


@pytest.mark.req("REQ-057")
@pytest.mark.req("REQ-040")
def test_voided_results_that_are_one_persons_are_withheld_with_the_figure_including_them():
    records = one_persons_voids()
    hides = one_reviewer_rule(records, 10)
    summary = voided_summary(records, hides)
    assert (summary.planted_errors, summary.results) == (2, 2)  # counts of work remain
    # Showing the figure that includes them would give them away by subtraction.
    assert summary.left_out.withheld and summary.with_them.withheld
    assert summary.with_them.total == summary.left_out.total == 0
    assert not summarise(records, places(records), MINIMUM, hides).catch.withheld
    check = judge(records, hides=hides)
    assert check.state is State.NOT_ENOUGH_DATA and check.withheld


@pytest.mark.req("REQ-057")
@pytest.mark.req("REQ-040")
def test_nothing_gives_away_scores_that_are_one_persons_by_subtraction():
    # What counts is r-1's alone, with too few: showing what was left out beside the figure
    # that includes it would reveal r-1's catch rate.
    records = planted_run([True, False, False, False], reviewer="r-1")
    records += voided(planted_run([True], reviewer="r-2"))
    records += voided(planted_run([False], reviewer="r-3"))
    hides = one_reviewer_rule(records, 10)
    summary = voided_summary(records, hides)
    assert summary.left_out.withheld and summary.with_them.withheld
    assert judge(records, hides=hides).withheld


@pytest.mark.req("REQ-057")
@pytest.mark.req("REQ-040")
def test_the_check_says_nothing_about_a_counted_figure_that_is_one_persons():
    # With the voided results, three people's catch rate is a breach. What is left is r-1's
    # alone, with too few, so the check must not say how it compares with the limit.
    records = planted_run([False] * 4, reviewer="r-1")
    records += voided(planted_run([False] * 4, reviewer="r-2"))
    records += voided(planted_run([False] * 4, reviewer="r-3"))
    no_share_limit = replace(LIMITS, voided_share_max=1.0)  # leaves only the comparison
    check = judge(records, no_share_limit, one_reviewer_rule(records, 10))
    assert check.state is State.NOT_ENOUGH_DATA and check.withheld
    assert "without them" not in check.detail


@pytest.mark.req("REQ-057")
def test_the_summary_counts_planted_errors_by_reason_and_results_by_how_they_went():
    first, second = planted_run([True, False])
    twice = [replace(first, planted_error_id="PE-A"), replace(second, planted_error_id="PE-A")]
    other = voided(planted_run([False]), "wrong_expected_decision")
    records = planted_run([True] * 10 + [False] * 2) + voided(twice) + other
    summary = voided_summary(records, nothing_hidden)
    assert summary.planted_errors == 2
    assert summary.reasons == {"not_defective": 1, "wrong_expected_decision": 1}
    assert (summary.results, summary.left_out.successes, summary.left_out.total) == (3, 1, 3)
    assert (summary.with_them.successes, summary.with_them.total) == (11, 15)
    assert voided_summary(planted_run([True]), nothing_hidden) is None
