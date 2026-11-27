"""Statistics helpers (step 2.2)."""

import pytest

from reviewproof.metrics import Rate, wilson


@pytest.mark.parametrize(
    ("successes", "total", "low", "high"),
    [
        # Newcombe, Statistics in Medicine 1998; 17: 857-872, method 3 (Wilson, uncorrected).
        (81, 263, 0.2553, 0.3662),
        (15, 148, 0.0624, 0.1605),
        (0, 20, 0.0000, 0.1611),
        (1, 29, 0.0061, 0.1718),
    ],
)
def test_wilson_matches_published_values_to_four_places(successes, total, low, high):
    result = wilson(successes, total)
    assert (round(result[0], 4), round(result[1], 4)) == (low, high)


@pytest.mark.parametrize("total", [1, 5, 10, 1000])
def test_the_ends_are_exact(total):
    assert wilson(0, total)[0] == 0.0
    assert wilson(total, total)[1] == 1.0


def test_nothing_to_measure():
    assert wilson(0, 0) is None
    empty = Rate(0, 0)
    assert empty.value is None and empty.interval is None


@pytest.mark.parametrize(("successes", "total"), [(-1, 5), (6, 5), (1, 0)])
def test_impossible_counts_are_refused(successes, total):
    if total == 0:
        assert wilson(successes, total) is None
    else:
        with pytest.raises(ValueError):
            wilson(successes, total)


def test_rate_of_outcomes():
    rate = Rate.of([True, False, True, True])
    assert (rate.successes, rate.total, rate.value) == (3, 4, 0.75)
    assert rate.interval[0] < 0.75 < rate.interval[1]


def test_more_data_narrows_the_range():
    narrow, wide = wilson(80, 100), wilson(8, 10)
    assert narrow[1] - narrow[0] < wide[1] - wide[0]
