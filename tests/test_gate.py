import copy
import dataclasses
import json
import random
import secrets
import statistics
import threading
from contextlib import closing
from math import sqrt
from pathlib import Path

import pytest

from reviewproof.config import load_config
from reviewproof.gate import Gate, GateError, due_probability, is_due
from reviewproof.models import Decision, EventType, new_item_id
from reviewproof.planted import ApprovalError, PlantedLibrary
from reviewproof.store import StoreError, open_oversight_db, open_planted_db
from reviewproof.store.commitments import payload_hash
from reviewproof.store.eventlog import EventLog, verify

DEMO = Path(__file__).parents[1] / "examples" / "deviation_triage"
ITEMS = json.loads((DEMO / "items.json").read_text(encoding="utf-8"))
QUEUE = "deviation-triage"


def config_with(rate=None, **changes):
    config = load_config(DEMO / "reviewproof.yaml")
    queue = dataclasses.replace(config.queues[QUEUE], planted_rate_override=rate, **changes)
    return dataclasses.replace(config, queues={QUEUE: queue})


# ----------------------------------------------------------------- the schedule


def simulate(rate, real_items, seed):
    """One flag per real item submitted: True where a planted error is released with it."""
    rng = random.Random(seed)
    return [is_due(rate, rng) for _ in range(real_items)]


def planted_share(released):
    return sum(released) / (len(released) + sum(released))


@pytest.mark.parametrize("rate", [0.01, 0.02, 0.03, 0.2])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_planted_share_is_within_a_fifth_of_a_point(rate, seed):
    # 200,000 items, not 10,000: with truly random draws, one standard deviation at 10,000 is
    # about 0.15 points, so a correct gate would miss a 0.2-point band in up to 18% of runs.
    assert abs(planted_share(simulate(rate, 200_000, seed)) - rate) < 0.002


def test_over_many_short_runs_the_share_averages_out():
    shares = [planted_share(simulate(0.02, 10_000, seed)) for seed in range(200)]
    assert abs(statistics.mean(shares) - 0.02) < 0.0005
    # The spread of independent draws: no hidden quota is smoothing the sequence.
    assert statistics.stdev(shares) == pytest.approx(sqrt(0.02 * 0.98 / 10_204), rel=0.2)


def runs_z(sequence):
    """Wald-Wolfowitz runs test: how far the number of runs is from a random sequence's."""
    ones = sum(sequence)
    zeros = len(sequence) - ones
    runs = 1 + sum(a != b for a, b in zip(sequence, sequence[1:], strict=False))
    mean = 2 * ones * zeros / len(sequence) + 1
    variance = (mean - 1) * (mean - 2) / (len(sequence) - 1)
    return (runs - mean) / sqrt(variance)


def gaps(sequence):
    positions = [i for i, planted in enumerate(sequence) if planted]
    return [b - a for a, b in zip(positions, positions[1:], strict=False)]


def periodicity(sequence, longest_lag=200):
    """The largest autocorrelation at any lag from 1, in standard errors."""
    n, share = len(sequence), sum(sequence) / len(sequence)
    positions = {i for i, planted in enumerate(sequence) if planted}
    worst = 0.0
    for lag in range(1, longest_lag + 1):
        pairs = sum(i + lag in positions for i in positions)
        expected = (n - lag) * share**2
        spread = sqrt(expected * (1 - share**2))
        worst = max(worst, abs(pairs - expected) / spread)
    return worst


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_there_is_no_pattern(seed):
    released = simulate(0.2, 200_000, seed)
    between = gaps(released)
    assert abs(runs_z(released)) < 3.5
    assert periodicity(released) < 4.5  # includes lag 1: nothing follows from the last item
    # Gaps vary as much as for random events. A fixed cadence gives 0.
    assert statistics.stdev(between) / statistics.mean(between) == pytest.approx(
        sqrt(1 - sum(released) / len(released)), rel=0.05
    )


def test_the_pattern_tests_would_catch_a_fixed_cadence():
    cadence = [i % 5 == 4 for i in range(100_000)]
    assert abs(runs_z(cadence)) > 50
    assert periodicity(cadence) > 50
    assert statistics.stdev(gaps(cadence)) == 0


def test_the_pattern_tests_would_catch_delayed_release():
    # The earlier design: each due planted error waited 1 to 5 submissions, and at most one
    # was released at a time. The queue this forms bunches releases together.
    rng, waiting, released = random.Random(1), [], []
    for _ in range(200_000):
        waiting = [wait - 1 for wait in waiting]
        ready = next((i for i, wait in enumerate(waiting) if wait <= 0), None)
        released.append(ready is not None)
        if ready is not None:
            del waiting[ready]
        if is_due(0.2, rng):
            waiting.append(rng.randint(1, 5))
    assert periodicity(released) > 10


def test_the_due_probability_accounts_for_the_planted_items_themselves():
    assert due_probability(0.5) == 1
    assert due_probability(0.02) == pytest.approx(1 / 49)


# ----------------------------------------------------------------- the gate


@pytest.fixture
def config():
    return config_with(rate=0.5)  # due on every submission, so tests are short


@pytest.fixture
def library(registry_db, config):
    return PlantedLibrary(registry_db, config)


def add(library, number, approve=True):
    payload = copy.deepcopy(ITEMS[1])
    payload.update(deviation_id=f"DEV-2026-{500 + number:04d}", classification="minor")
    planted_error_id = f"PE-{number}"
    library.import_data(
        {
            "planted_error_id": planted_error_id,
            "queue": QUEUE,
            "defect_class": "wrong_classification",
            "defect_field": "classification",
            "expected_decision": "reject",
            "author": "author-1",
            "payload": payload,
        }
    )
    if approve:
        library.approve(planted_error_id, "qa-1")
        library.approve(planted_error_id, "qa-2")
    return planted_error_id


@pytest.fixture
def gate(registry_db, oversight_db, config):
    return Gate(registry_db, oversight_db, config, rng=random.Random(7))


def submit(gate, log, injection):
    """What the API will do with an injection (step 1.8)."""
    log.append(
        EventType.SUBMITTED,
        system_id="sys-1",
        queue_id=QUEUE,
        item_id=injection.item_id,
        data={
            "commitment": injection.commitment,
            "payload_hash": payload_hash(injection.planted.payload, injection.payload_salt),
        },
    )


def decide(log, injection, reviewer="r-1", shown=True):
    if shown:
        log.append(
            EventType.SHOWN, system_id="sys-1", item_id=injection.item_id, reviewer_id=reviewer
        )
    log.append(
        EventType.DECIDED,
        system_id="sys-1",
        item_id=injection.item_id,
        reviewer_id=reviewer,
        decision=Decision.REJECT,
    )


def next_injection(gate, reviewer=None, tries=40):
    for _ in range(tries):
        injection = gate.on_submission(QUEUE, reviewer)
        if injection:
            return injection
    return None


def test_only_approved_planted_errors_are_used(gate, library, event_log):
    add(library, 1, approve=False)
    add(library, 2)
    library.retire(add(library, 3), "qa-1")
    for _ in range(5):
        injection = next_injection(gate)
        assert injection.planted.planted_error_id == "PE-2"
        submit(gate, event_log, injection)
        decide(event_log, injection)


def test_nothing_is_released_without_approved_planted_errors(gate, library):
    add(library, 1, approve=False)
    assert next_injection(gate) is None
    status = gate.status(QUEUE)
    assert (status.usable, status.free) == (0, 0)
    assert status.shortfalls > 0


def test_an_injection_is_registered_before_it_is_returned(
    gate, library, registry_db, oversight_db, event_log
):
    add(library, 1)
    injection = next_injection(gate, reviewer="r-9")
    row = registry_db.execute("SELECT * FROM item_registry").fetchone()
    assert (row["item_id"], row["kind"]) == (injection.item_id, "planted")
    assert row["target_id"] == injection.target_id == injection.planted.reserved_target_id
    assert (row["salt"], row["payload_salt"]) == (injection.salt, injection.payload_salt)
    assert row["assigned_reviewer"] == "r-9"
    assert injection.salt != injection.payload_salt
    submit(gate, event_log, injection)
    result = verify(oversight_db, registry_db)
    assert result.ok, result.registry_errors


def test_a_planted_item_id_looks_like_any_other(gate, library):
    add(library, 1)
    injection = next_injection(gate)
    assert len(injection.item_id) == len(new_item_id())
    int(injection.item_id, 16)


def test_the_injection_repr_reveals_nothing(gate, library):
    add(library, 1)
    injection = next_injection(gate)
    text = repr(injection)
    for secret in ("PE-1", "DEV-2026-0501", injection.target_id, injection.commitment):
        assert secret not in text


def test_no_second_copy_while_one_awaits_a_decision(gate, library, event_log):
    add(library, 1)
    first = next_injection(gate)
    submit(gate, event_log, first)
    assert next_injection(gate) is None
    assert gate.status(QUEUE).free == 0
    decide(event_log, first)
    assert gate.status(QUEUE).free == 1
    assert next_injection(gate).planted.planted_error_id == "PE-1"


def test_being_shown_is_not_enough_to_free_a_copy(gate, library, event_log):
    add(library, 1)
    first = next_injection(gate)
    submit(gate, event_log, first)
    event_log.append(EventType.SHOWN, system_id="sys-1", item_id=first.item_id, reviewer_id="r-1")
    assert next_injection(gate) is None


def test_least_used_go_first(gate, library, event_log):
    for number in range(4):
        add(library, number)
    order = []
    for _ in range(12):
        injection = next_injection(gate)
        order.append(injection.planted.planted_error_id)
        submit(gate, event_log, injection)
        decide(event_log, injection)
    for start in (0, 4, 8):  # each round uses every planted error once
        assert sorted(order[start : start + 4]) == ["PE-0", "PE-1", "PE-2", "PE-3"]
    assert len({tuple(order[s : s + 4]) for s in (0, 4, 8)}) > 1  # in varying order


def test_a_named_reviewer_does_not_get_the_same_one_twice(gate, library, event_log):
    for number in range(3):
        add(library, number)
    given = []
    while (injection := next_injection(gate, reviewer="r-1")) is not None:
        given.append(injection.planted.planted_error_id)
        submit(gate, event_log, injection)
        decide(event_log, injection, "r-1")
    assert sorted(given) == ["PE-0", "PE-1", "PE-2"]
    # Someone else can still be given them.
    assert next_injection(gate, reviewer="r-2") is not None


def test_being_assigned_counts_even_if_someone_else_decided(gate, library, event_log):
    add(library, 1)
    first = next_injection(gate, reviewer="r-1")
    submit(gate, event_log, first)
    decide(event_log, first, reviewer="r-2")
    assert next_injection(gate, reviewer="r-1") is None
    assert next_injection(gate, reviewer="r-2") is None


@pytest.mark.parametrize("shown", [True, False])
def test_having_met_it_counts_without_an_assignment(gate, library, event_log, shown):
    add(library, 1)
    first = next_injection(gate)  # the host did not know the reviewer
    submit(gate, event_log, first)
    decide(event_log, first, reviewer="r-1", shown=shown)
    assert next_injection(gate, reviewer="r-1") is None
    assert next_injection(gate, reviewer="r-2") is not None


def test_without_a_reviewer_a_repeat_is_possible(gate, library, event_log):
    # The known limit of reuse (ADR-003): scoring must discount repeat sightings.
    add(library, 1)
    first = next_injection(gate)
    submit(gate, event_log, first)
    decide(event_log, first, reviewer="r-1")
    assert next_injection(gate).planted.planted_error_id == "PE-1"


def test_an_unused_registry_row_holds_its_planted_error_back(gate, library):
    # A crash between the registry and the log leaves a row that never gets a decision.
    add(library, 1)
    next_injection(gate)
    assert next_injection(gate) is None


def test_a_used_planted_error_cannot_gain_approvals(registry_db, oversight_db, event_log):
    lenient = config_with(rate=0.5, gxp=False)
    library = PlantedLibrary(registry_db, lenient)
    add(library, 1, approve=False)
    library.approve("PE-1", "qa-1")
    gate = Gate(registry_db, oversight_db, lenient, rng=random.Random(7))
    submit(gate, event_log, next_injection(gate))

    strict = PlantedLibrary(registry_db, config_with(rate=0.5, gxp=True))
    with pytest.raises(ApprovalError, match="has been used"):
        strict.approve("PE-1", "qa-2")
    assert strict.available(QUEUE) == []
    assert verify(oversight_db, registry_db).ok
    strict.retire("PE-1", "qa-1")
    assert verify(oversight_db, registry_db).ok


def test_state_carries_over_to_a_new_gate(registry_db, oversight_db, config, library):
    add(library, 1)
    assert Gate(registry_db, oversight_db, config).on_submission(QUEUE) is not None
    later = Gate(registry_db, oversight_db, config)  # as after a restart
    assert later.on_submission(QUEUE) is None  # the first copy is still waiting


def test_each_shortfall_is_recorded_and_nothing_is_saved_up(gate, library, event_log):
    for _ in range(25):
        assert gate.on_submission(QUEUE) is None
    assert gate.status(QUEUE).shortfalls == 25
    add(library, 1)
    first = gate.on_submission(QUEUE)
    assert first is not None
    submit(gate, event_log, first)
    decide(event_log, first)
    add(library, 2)
    # No backlog from the 25 misses: still one at most per submission.
    assert gate.on_submission(QUEUE).planted.planted_error_id == "PE-2"
    assert gate.status(QUEUE).shortfalls == 25


def test_nothing_is_written_when_no_planted_error_is_due(registry_db, oversight_db):
    gate = Gate(registry_db, oversight_db, config_with(rate=0.001), rng=random.Random(1))
    before = registry_db.total_changes
    assert all(gate.on_submission(QUEUE) is None for _ in range(20))
    assert registry_db.total_changes == before


def test_queues_are_scheduled_separately(registry_db, oversight_db, config, library):
    other = dataclasses.replace(config.queues[QUEUE], name="other")
    both = dataclasses.replace(config, queues={**config.queues, "other": other})
    add(library, 1)
    gate = Gate(registry_db, oversight_db, both, rng=random.Random(1))
    assert all(gate.on_submission("other") is None for _ in range(40))
    assert next_injection(gate) is not None
    assert gate.status("other").usable == 0


def test_status_reports_stale_and_dropped(gate, library, registry_db, oversight_db, tmp_path):
    add(library, 1)
    assert gate.status(QUEUE).stale == {}
    schema = tmp_path / "schema.json"
    schema.write_text('{"properties": {"classification": {}}, "required": ["site"]}', "utf-8")
    changed = Gate(
        registry_db, oversight_db, config_with(rate=0.5, item_schema=schema), random.Random(1)
    )
    assert next_injection(changed) is None
    status = changed.status(QUEUE)
    assert list(status.stale) == ["PE-1"]
    assert (status.usable, status.planted_rate) == (0, 0.5)
    assert status.shortfalls > 0


def test_the_default_random_source_is_unpredictable(registry_db, oversight_db, config):
    assert isinstance(Gate(registry_db, oversight_db, config)._rng, secrets.SystemRandom)


def test_many_planted_errors_are_looked_up_in_chunks(gate, library, event_log, monkeypatch):
    monkeypatch.setattr("reviewproof.gate._CHUNK", 2)
    for number in range(5):
        add(library, number)
    used = []
    for _ in range(5):
        injection = next_injection(gate, reviewer="r-1")
        used.append(injection.planted.planted_error_id)
        submit(gate, event_log, injection)
        decide(event_log, injection)
    assert len(set(used)) == 5
    assert next_injection(gate, reviewer="r-1") is None


@pytest.mark.parametrize("reviewer", ["", "  ", 5])
def test_a_bad_reviewer_is_refused(gate, reviewer):
    with pytest.raises(GateError, match="reviewer"):
        gate.on_submission(QUEUE, reviewer)


def test_an_unknown_queue_is_refused(gate):
    with pytest.raises(GateError, match="not configured"):
        gate.on_submission("nope")
    with pytest.raises(GateError):
        gate.status("nope")


def test_an_unreadable_log_releases_nothing(gate, library, registry_db, oversight_db):
    add(library, 1)
    # A used planted error forces the gate to read the log.
    registry_db.execute(
        "INSERT INTO item_registry VALUES ('x', 'planted', 'PE-1', 't', zeroblob(32), "
        "zeroblob(32), NULL)"
    )
    oversight_db.close()
    with pytest.raises(StoreError, match="event log"):
        gate.on_submission(QUEUE)
    assert registry_db.execute("SELECT COUNT(*) FROM item_registry").fetchone()[0] == 1
    assert not registry_db.in_transaction


def test_a_missing_table_raises_store_error(gate, registry_db):
    registry_db.execute("DROP TABLE gate_shortfalls")
    with pytest.raises(StoreError, match="cannot update"):
        gate.on_submission(QUEUE)
    with pytest.raises(StoreError, match="gate's state"):
        gate.status(QUEUE)


def test_a_missing_registry_raises_store_error(gate, library, registry_db):
    add(library, 1)
    registry_db.execute("DROP TABLE item_registry")
    with pytest.raises(StoreError, match="item registry"):
        gate.status(QUEUE)


def test_a_callers_transaction_is_left_alone(gate, registry_db):
    registry_db.execute("BEGIN")
    with pytest.raises(StoreError, match="already in a transaction"):
        gate.on_submission(QUEUE)
    assert registry_db.in_transaction
    registry_db.execute("ROLLBACK")


def test_concurrent_gates_never_release_two_copies(
    library, config, planted_path, oversight_path, oversight_db, registry_db
):
    for number in range(3):
        add(library, number)
    released, errors = [], []
    start = threading.Barrier(6)

    def worker():
        try:
            with (
                closing(open_planted_db(planted_path)) as planted,
                closing(open_oversight_db(oversight_path)) as oversight,
            ):
                gate = Gate(planted, oversight, config)
                start.wait()
                for _ in range(15):
                    injection = gate.on_submission(QUEUE)
                    if injection:
                        released.append(injection.planted.planted_error_id)
        except BaseException as error:  # reported in the main thread
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sorted(released) == ["PE-0", "PE-1", "PE-2"]  # none is decided, so none repeats


def test_events_are_read_after_they_are_written(gate, library, event_log, oversight_path):
    # The gate's own connection to the log sees decisions made through another one.
    add(library, 1)
    first = next_injection(gate)
    with closing(open_oversight_db(oversight_path)) as other:
        log = EventLog(other)
        submit(gate, log, first)
        decide(log, first)
    assert next_injection(gate) is not None
