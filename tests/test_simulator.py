"""The reviewer simulator (step 2.8)."""

import random
from datetime import datetime, timezone
from statistics import median

import pytest
from deploy import ITEMS

from reviewproof.models import Decision
from reviewproof.planted import PlantedError
from reviewproof.simulator import (
    CAREFUL,
    RUBBER_STAMP,
    Profile,
    SimulatedClock,
    SimulatedReviewer,
)

PLANTED_PAYLOAD = {**ITEMS[1], "classification": "minor"}


def planted_error(expected="edit:classification", correct="major"):
    return PlantedError(
        planted_error_id="PE-1",
        queue="q",
        defect_class="wrong_classification",
        defect_field="classification",
        expected_decision=expected,
        author="a",
        imported_at="2026-11-26T10:00:00.000000Z",
        payload=PLANTED_PAYLOAD,
        reserved_target_id="rp-reserved-1",
        has_correct_value=correct is not None,
        correct_value=correct,
    )


def reviewer(profile, planted=None, seed=1):
    return SimulatedReviewer("r", profile, [planted or planted_error()], random.Random(seed))


def catches(profile, tries=4000, **arguments):
    person = reviewer(profile)
    return (
        sum(
            person.judge(PLANTED_PAYLOAD, **arguments)[0] is not Decision.APPROVE
            for _ in range(tries)
        )
        / tries
    )


@pytest.mark.parametrize(("profile", "median_time"), [(CAREFUL, 90), (RUBBER_STAMP, 8)])
def test_review_times_are_spread_around_the_median(profile, median_time):
    person = reviewer(profile)
    times = [person.seconds() for _ in range(4000)]
    assert median(times) == pytest.approx(median_time, rel=0.06)
    assert min(times) > 0 and max(times) > 2 * median_time  # a long tail


@pytest.mark.parametrize(("profile", "rate"), [(CAREFUL, 0.90), (RUBBER_STAMP, 0.30)])
def test_catch_probabilities(profile, rate):
    assert catches(profile) == pytest.approx(rate, abs=0.03)


def test_real_items_are_mostly_approved():
    careful, rubber = reviewer(CAREFUL), reviewer(RUBBER_STAMP)

    def agreement(person):
        return sum(person.judge(ITEMS[0])[0] is Decision.APPROVE for _ in range(4000)) / 4000

    assert agreement(careful) == pytest.approx(0.92, abs=0.02)
    assert agreement(rubber) > 0.985


def test_a_catch_corrects_the_field_when_that_is_expected():
    person = reviewer(Profile("sure", 10, 0.1, 1.0))
    decision, final = person.judge(PLANTED_PAYLOAD)
    assert decision is Decision.EDIT
    assert final == {**PLANTED_PAYLOAD, "classification": "major"}


@pytest.mark.parametrize(("expected", "correct"), [("reject", "major"), ("reject", None)])
def test_otherwise_a_catch_rejects(expected, correct):
    planted = planted_error(expected, correct)
    person = reviewer(Profile("sure", 10, 0.1, 1.0), planted)
    assert person.judge(PLANTED_PAYLOAD) == (Decision.REJECT, None)


def test_an_edit_to_real_work_changes_something():
    person = reviewer(Profile("editor", 10, 0.1, 1.0, edit_real=1.0))
    decision, final = person.judge(ITEMS[0])
    assert decision is Decision.EDIT and final != ITEMS[0]
    with pytest.raises(ValueError, match="no text"):
        person.judge({"count": 1})


def test_a_tiring_reviewer_catches_less_later():
    tiring = Profile("tiring", 10, 0.1, 0.9, tiring_per_item=0.02)
    assert catches(tiring, position=0) > catches(tiring, position=30) + 0.4
    assert catches(tiring, position=100) == 0


def test_the_clock_only_goes_forward():
    clock = SimulatedClock(datetime(2026, 3, 2, 9, tzinfo=timezone.utc))
    clock.advance(1.5)
    assert clock() == "2026-03-02T09:00:01.500000Z"
    clock.move_to(datetime(2026, 3, 3, tzinfo=timezone.utc))
    assert clock().startswith("2026-03-03T00:00:00")
    with pytest.raises(ValueError):
        clock.move_to(datetime(2026, 3, 2, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="timezone"):
        SimulatedClock(datetime(2026, 3, 2))
