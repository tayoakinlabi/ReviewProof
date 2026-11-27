"""The metrics must reproduce the values worked out by hand (steps 2.1 to 2.4)."""

import golden_log
import pytest
from deploy import QUEUE

from reviewproof.metrics import load
from reviewproof.metrics.measures import sessions, summarise


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    return golden_log.build(tmp_path_factory.mktemp("golden"))


@pytest.fixture(scope="module")
def projection(golden):
    return load(golden.db, golden.planted_db)


@pytest.fixture(scope="module")
def places(golden, projection):
    gaps = {QUEUE: golden.config.queues[QUEUE].thresholds.session_gap_minutes}
    return sessions(projection.records, gaps)


def test_every_scenario_item_is_projected(golden, projection):
    # 19 real items (R1-R19) and 18 planted: P01-P12, Q01, Q02, Q04, Q07, X01 and X02.
    assert len(projection.records) == len(golden.ids) == 37
    assert sum(r.planted for r in projection.records) == 18
    assert {r.queue for r in projection.records} == {QUEUE}
    [context] = projection.contexts.values()
    assert context.system == "deviation-classifier"
    assert "prompt 1" in context.label


def test_each_decided_item_matches_the_hand_built_table(golden, projection, places):
    expected = {row["label"]: row for row in golden_log.read("expected_items.csv")}
    decided = [r for r in projection.records if r.decided]
    assert len(decided) == len(expected) == 34
    for record in decided:
        label = golden.label_of(record.item_id)
        row, place = expected[label], places[record.item_id]
        actual = {
            "label": label,
            "planted_error": record.planted_error_id or "",
            "defect_class": record.defect_class.value if record.defect_class else "",
            "reviewer": record.reviewer,
            "dwell_s": str(round(record.dwell_ms / 1000)),
            "outcome": record.outcome.value if record.outcome else "",
            "first_sight": {None: "", True: "yes", False: "no"}[record.first_sight],
            "session": str(place.session),
            "position": str(place.position),
            "hour": str(place.hour),
            "half": "early" if place.early else "late",
        }
        assert actual == row, label
        assert record.dwell_ms == int(row["dwell_s"]) * 1000


def test_undecided_items(golden, projection):
    undecided = {golden.label_of(r.item_id): r for r in projection.records if not r.decided}
    assert set(undecided) == {"R18", "R19", "X02"}
    assert undecided["R19"].showings == ()
    assert undecided["X02"].planted_error_id == "PE-02"
    assert undecided["X02"].outcome is None and undecided["X02"].dwell_ms is None


def values(summary):
    """The summary as (metric -> (successes, total, value)), named as in the expected table."""
    found = {}

    def rate(name, result):
        found[name] = (result.successes, result.total, result.value)

    def number(name, value):
        found[name] = (None, None, value)

    number("decided", summary.decided)
    number("decided_real", summary.decided_real)
    rate("catch_rate", summary.catch)
    number("catch_rate_low", summary.catch.interval[0])
    number("catch_rate_high", summary.catch.interval[1])
    for defect, result in summary.classes.items():
        rate(f"class_{defect.value}", result)
    rate("fast_approval_share", summary.fast)
    number("fast_approval_low", summary.fast.interval[0])
    number("fast_approval_high", summary.fast.interval[1])
    number("median_dwell_ms", summary.dwell_ms)
    rate("agreement_rate", summary.agreement)
    rate("repeat_catch_rate", summary.repeats)
    number("pending_planted", summary.pending_planted)
    for name, value in vars(summary.load).items():
        number(f"load_{name}", value)
    rate("fatigue_early", summary.fatigue.early)
    rate("fatigue_late", summary.fatigue.late)
    number("fatigue_drop", summary.fatigue.drop)
    for label, result in summary.fatigue.by_position.items():
        rate(f"position_{label}", result)
    for label, result in summary.fatigue.by_hour.items():
        rate(f"hour_{label}", result)
    return found


@pytest.mark.parametrize("scope", ["queue", "A", "B"])
def test_each_metric_matches_the_hand_value(golden, projection, places, scope):
    records = projection.records
    if scope != "queue":
        records = [r for r in records if r.reviewer == scope]
    minimum = {QUEUE: golden.config.queues[QUEUE].min_review_seconds}
    actual = values(summarise(records, places, minimum))
    expected = [r for r in golden_log.read("expected_metrics.csv") if r["scope"] == scope]
    assert expected
    for row in expected:
        successes, total, value = actual[row["metric"]]
        where = f"{scope} {row['metric']}"
        if row["successes"]:
            assert (successes, total) == (int(row["successes"]), int(row["total"])), where
        assert round(value, 4) == float(row["value"]), where


def test_the_queue_table_covers_every_class_and_bucket(golden, projection, places):
    summary = summarise(projection.records, places, {QUEUE: 30})
    named = {r["metric"] for r in golden_log.read("expected_metrics.csv") if r["scope"] == "queue"}
    assert {f"class_{d.value}" for d in summary.classes} <= named
    assert {f"position_{k}" for k in summary.fatigue.by_position} <= named
    assert {f"hour_{k}" for k in summary.fatigue.by_hour} <= named
