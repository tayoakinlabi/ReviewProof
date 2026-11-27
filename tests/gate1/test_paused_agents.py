"""Gate 1 for paused agents (ADR-005), passed before any adapter is written.

An agent library runs code again, resumes twice, replays old checkpoints and can approve by
itself. None of that may write anything that was not reviewed, write anything twice, execute a
planted item, or give a planted error a second chance to be drawn.
"""

import json
import random
import threading
from collections import Counter
from contextlib import closing

import pytest
from deploy import ITEMS, QUEUE, item
from helpers import triggers_removed

from reviewproof import (
    CallState,
    Decision,
    GuardError,
    Item,
    Oversight,
    OversightError,
    TransitionError,
    UnknownTokenError,
)
from reviewproof.models import EventType
from reviewproof.store import StoreError, open_oversight_db, open_planted_db


class Writes:
    """Stands in for the host's write function."""

    def __init__(self):
        self.done = []

    def __call__(self, target_id, payload):
        assert not target_id.startswith("rp-reserved-"), "a planted item reached the host"
        self.done.append((target_id, payload))
        return "written"


@pytest.fixture
def writes():
    return Writes()


def proposal(number):
    base = item(number % len(ITEMS))
    payload = {**base.payload, "deviation_id": f"DEV-2025-{number:04d}"}
    return Item(payload=payload, target_id=f"QMS-{number}")


def keyed_pair(ov, reviewer=None):
    """Submit with keys until a planted item comes. Returns (key, item, real, planted)."""
    for number in range(40):
        key, submitted = f"run-1/call-{number}", proposal(number)
        tickets = ov.submit(submitted, reviewer, key=key)
        if len(tickets) == 2:
            real = next(t for t in tickets if t.payload == submitted.payload)
            planted = next(t for t in tickets if t is not real)
            return key, submitted, real, planted
    raise AssertionError("no planted item was released")


def decide(ov, ticket, decision="approve", final=None, reviewer="r-1"):
    ov.shown(ticket.token, reviewer)
    ov.decided(ticket.token, reviewer, decision, final_payload=final)


def log_size(deployment):
    return deployment.query("SELECT COUNT(*) AS n FROM events")[0]["n"]


def uses_of_planted_errors(deployment):
    sql = "SELECT COUNT(*) AS n FROM item_registry WHERE kind = 'planted'"
    return deployment.query(sql, planted=True)[0]["n"]


# ----------------------------------------------------------------- repeating submit


class AlwaysDue(random.Random):
    """Every draw releases a planted error, so a repeat that drew again would show."""

    def __init__(self):
        super().__init__(1)
        self.draws = 0

    def random(self):
        self.draws += 1
        return 0.0


@pytest.mark.req("REQ-047")
def test_repeating_a_key_returns_the_same_tickets_and_draws_once(ov, deployment):
    rng = AlwaysDue()
    ov._rng = ov._gate._rng = rng
    first = ov.submit(proposal(1), key="k")
    assert len(first) == 2
    draws, events, uses = rng.draws, log_size(deployment), uses_of_planted_errors(deployment)
    for _ in range(25):
        again = ov.submit(proposal(1), key="k")
        assert [(t.token, list(t.payload.items())) for t in again] == [
            (t.token, list(t.payload.items())) for t in first
        ]
    assert (rng.draws, log_size(deployment), uses_of_planted_errors(deployment)) == (
        draws,
        events,
        uses,
    )
    assert deployment.verify().ok


def test_a_key_without_a_planted_item_never_gains_one(make):
    deployment = make(rate=0.3)  # no planted errors loaded yet
    ov = deployment.open()
    ov._gate._rng = AlwaysDue()
    [first] = ov.submit(proposal(1), key="k")
    deployment.load_planted(ov)
    [again] = ov.submit(proposal(1), key="k")
    assert again.token == first.token


def test_tickets_survive_a_restart(ov, deployment):
    key, submitted, real, planted = keyed_pair(ov)
    other = deployment.open(seed=99)
    assert {t.token for t in other.submit(submitted, key=key)} == {real.token, planted.token}


def test_repeating_after_decisions_and_execution_changes_nothing(ov, deployment, writes):
    key, submitted, real, planted = keyed_pair(ov)
    decide(ov, real)
    decide(ov, planted)
    ov.settle(planted.token, planted.payload)
    ov.execute_call(key, submitted.payload, writes)
    events = log_size(deployment)
    assert {t.token for t in ov.submit(submitted, key=key)} == {real.token, planted.token}
    assert log_size(deployment) == events


@pytest.mark.req("REQ-047")
@pytest.mark.parametrize("change", ["payload", "target", "reviewer", "reviewer-added"])
def test_a_key_cannot_be_used_for_anything_else(ov, deployment, change):
    first = None if change == "reviewer-added" else "r-1"
    submitted = proposal(1)
    ov.submit(submitted, first, key="k")
    events, uses = log_size(deployment), uses_of_planted_errors(deployment)
    reviewer = first
    if change == "payload":
        submitted = Item({**submitted.payload, "deviation_id": "DEV-9"}, submitted.target_id)
    elif change == "target":
        submitted = Item(submitted.payload, "QMS-other")
    else:
        reviewer = "r-2"
    with pytest.raises(OversightError, match="already used"):
        ov.submit(submitted, reviewer, key="k")
    assert (log_size(deployment), uses_of_planted_errors(deployment)) == (events, uses)


def test_a_planted_payload_cannot_claim_the_key(ov):
    key, submitted, _, planted = keyed_pair(ov)
    with pytest.raises(OversightError, match="already used"):
        ov.submit(Item(planted.payload, submitted.target_id), key=key)


@pytest.mark.parametrize("key", ["", " k", "k ", "x" * 501, 7, "\ud800"])
def test_bad_keys_are_refused_before_anything_is_written(ov, deployment, key):
    events = log_size(deployment)
    with pytest.raises(OversightError, match="key must be"):
        ov.submit(proposal(1), key=key)
    assert log_size(deployment) == events


def test_a_submission_that_never_reached_the_log_starts_afresh(ov, deployment, monkeypatch):
    def broken(*arguments, **keywords):
        raise StoreError("the log is down")

    with monkeypatch.context() as patch:
        patch.setattr(ov._log, "append", broken)
        with pytest.raises(StoreError):
            ov.submit(proposal(1), key="k")
    assert log_size(deployment) == 1
    tickets = ov.submit(proposal(1), key="k")
    assert {t.token for t in ov.submit(proposal(1), key="k")} == {t.token for t in tickets}
    assert deployment.verify().ok


def test_concurrent_calls_with_one_key_make_one_submission(ov, deployment):
    results, errors = [], []

    def submit(number):
        try:
            with Oversight(deployment.config, QUEUE, deployment.db, deployment.planted_db) as own:
                results.append(frozenset(t.token for t in own.submit(proposal(1), key="k")))
        except Exception as error:  # reported below
            errors.append(error)

    threads = [threading.Thread(target=submit, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(set(results)) == 1
    sql = "SELECT COUNT(*) AS n FROM events WHERE type = 'submitted'"
    assert deployment.query(sql)[0]["n"] == len(results[0])


def test_a_pair_is_not_repeated_while_the_item_schema_is_unusable(ov, deployment):
    """Without the schema, a planted item's fields could not be ordered like a real one's."""
    key, submitted, _, _ = keyed_pair(ov)
    (deployment.folder / "item.schema.json").write_text("not a schema", encoding="utf-8")
    events = log_size(deployment)
    with pytest.raises(OversightError, match="item schema cannot be used"):
        ov.submit(submitted, key=key)
    assert log_size(deployment) == events


# ----------------------------------------------------------------- what is stored and shown


def test_keyed_tickets_and_records_look_alike(ov, deployment):
    key, _, real, planted = keyed_pair(ov)
    plain = ov.submit(proposal(100))[0]
    assert len(real.token) == len(planted.token) == len(plain.token)
    rows = deployment.query(
        "SELECT * FROM submission_keys WHERE submission_key = ?", (key,), planted=True
    )
    assert len(rows) == 2
    assert len({(r["queue_id"], r["seed"], r["reviewer"]) for r in rows}) == 1
    log = json.dumps(deployment.query("SELECT * FROM events"))
    assert key not in log
    for ticket in (real, planted):
        assert ticket.token not in log


def test_the_key_table_cannot_be_changed(ov, deployment):
    keyed_pair(ov)
    with closing(open_planted_db(deployment.planted_db)) as db:
        for statement in (
            "UPDATE submission_keys SET submission_key = 'x'",
            "DELETE FROM submission_keys",
        ):
            with pytest.raises(Exception, match="cannot be"):
                db.execute(statement)


# ----------------------------------------------------------------- planted never runs


def test_a_rubber_stamping_reviewer_and_an_eager_agent(make, writes):
    """Many calls, every item approved, every call resumed several times and early."""
    deployment = make(rate=0.3)
    ov = deployment.open(seed=5)
    deployment.load_planted(ov)
    expected, planted_count = {}, 0
    for number in range(120):
        key, submitted = f"run/{number}", proposal(number)
        tickets = ov.submit(submitted, key=key)
        planted_count += len(tickets) - 1
        with pytest.raises(TransitionError):  # resumed before any decision
            ov.execute_call(key, submitted.payload, writes)
        ordered = sorted(tickets, key=lambda t: t.payload == submitted.payload)
        for ticket in ordered:  # the planted one first, when there is one
            decide(ov, ticket)
            state = ov.settle(ticket.token, ticket.payload)
            real = ticket.payload == submitted.payload
            assert state.key == key
            assert state.state is (CallState.APPROVE if real else CallState.WAITING)
            assert ov.settle(ticket.token, ticket.payload) == state
            if not real:
                with pytest.raises(TransitionError):  # the planted approval wakes nothing
                    ov.execute_call(key, submitted.payload, writes)
        assert ov.execute_call(key, submitted.payload, writes).executed
        for _ in range(2):
            with pytest.raises(TransitionError):
                ov.execute_call(key, submitted.payload, writes)
        expected[submitted.target_id] = submitted.payload

    assert planted_count > 20
    assert dict(writes.done) == expected
    assert len(writes.done) == 120
    events = Counter(r["type"] for r in deployment.query("SELECT type FROM events"))
    assert events["executed"] == 120
    assert events["blocked"] == events["withdrawn"] == planted_count
    assert events["execute_requested"] == 120 + planted_count
    assert deployment.verify().ok


@pytest.mark.req("REQ-048")
def test_approving_only_the_planted_item_lets_nothing_run(ov, writes):
    key, submitted, _, planted = keyed_pair(ov)
    decide(ov, planted)
    assert ov.settle(planted.token, planted.payload).state is CallState.WAITING
    for payload in (submitted.payload, planted.payload):
        with pytest.raises(TransitionError):
            ov.execute_call(key, payload, writes)
    assert writes.done == []


@pytest.mark.req("REQ-048")
def test_a_planted_payload_cannot_run_under_the_real_approval(ov, writes):
    key, _, real, planted = keyed_pair(ov)
    decide(ov, real)
    with pytest.raises(GuardError, match="Nothing was executed"):
        ov.execute_call(key, planted.payload, writes)
    assert writes.done == []


@pytest.mark.req("REQ-048")
@pytest.mark.parametrize("real_decision", ["reject", None])
def test_a_resume_without_approval_runs_nothing(ov, writes, real_decision):
    key, submitted, real, _ = keyed_pair(ov)
    if real_decision:
        decide(ov, real, real_decision)
    with pytest.raises(TransitionError):
        ov.execute_call(key, submitted.payload, writes)
    assert writes.done == []


def test_an_edited_call_runs_only_the_edited_content_once(ov, writes):
    key, submitted, real, _ = keyed_pair(ov)
    edited = {**submitted.payload, "deviation_id": "DEV-edited"}
    decide(ov, real, "edit", edited)
    assert ov.settle(real.token, edited).state is CallState.EDIT
    with pytest.raises(GuardError):  # what the library would replay: the proposal
        ov.execute_call(key, submitted.payload, writes)
    ov.execute_call(key, edited, writes)
    with pytest.raises(TransitionError):
        ov.execute_call(key, edited, writes)
    assert writes.done == [(submitted.target_id, edited)]


def test_an_unknown_key_runs_nothing(ov, writes):
    with pytest.raises(OversightError, match="nothing in this queue"):
        ov.execute_call("never-submitted", {}, writes)
    with pytest.raises(OversightError, match="nothing in this queue"):
        ov.call_status("never-submitted")
    assert writes.done == []


def test_concurrent_resumes_write_once(ov, deployment):
    key, submitted, real, _ = keyed_pair(ov)
    decide(ov, real)
    done, refused = [], []

    def resume():
        with Oversight(deployment.config, QUEUE, deployment.db, deployment.planted_db) as own:
            try:
                own.execute_call(key, submitted.payload, lambda *call: done.append(call))
            except OversightError as error:
                refused.append(error)

    threads = [threading.Thread(target=resume) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert (len(done), len(refused)) == (1, 7)


# ----------------------------------------------------------------- settle


@pytest.mark.req("REQ-017")
@pytest.mark.parametrize("which", ["real", "planted"])
def test_settle_refuses_both_kinds_for_the_same_reasons(ov, deployment, which):
    _, _, real, planted = keyed_pair(ov)
    ticket = real if which == "real" else planted
    with pytest.raises(TransitionError, match="^this item has no decision$"):
        ov.settle(ticket.token, ticket.payload)
    decide(ov, ticket)
    with pytest.raises(OversightError, match="needs the payload"):
        ov.settle(ticket.token)
    wrong = {**ticket.payload, "deviation_id": "DEV-wrong"}
    with pytest.raises(GuardError) as refusal:
        ov.settle(ticket.token, wrong)
    assert (
        str(refusal.value) == "The payload is not the one that was approved. Nothing was executed."
    )
    events = deployment.query("SELECT type, data FROM events ORDER BY event_id DESC LIMIT 1")
    assert events[0]["type"] == "blocked" and "payload_changed" in events[0]["data"]
    ov.settle(ticket.token, ticket.payload)  # a refusal does not use up anything


def test_settling_a_real_item_records_nothing(ov, deployment, writes):
    key, submitted, real, _ = keyed_pair(ov)
    decide(ov, real)
    events = log_size(deployment)
    ov.settle(real.token, real.payload)
    assert log_size(deployment) == events
    assert ov.execute_call(key, submitted.payload, writes).executed
    ov.settle(real.token, real.payload)  # the agent was quicker than the host's retry
    assert len(writes.done) == 1


@pytest.mark.parametrize("which", ["real", "planted"])
def test_a_rejection_settles_without_a_payload_and_records_nothing(ov, deployment, which):
    _, _, real, planted = keyed_pair(ov)
    ticket = real if which == "real" else planted
    decide(ov, ticket, "reject")
    events = log_size(deployment)
    state = ov.settle(ticket.token)
    assert state.state is (CallState.REJECT if which == "real" else CallState.WAITING)
    assert log_size(deployment) == events


def test_settle_needs_a_keyed_item_and_a_known_token(ov):
    [ticket, *_] = ov.submit(proposal(1))
    decide(ov, ticket)
    with pytest.raises(OversightError, match="not submitted with a key"):
        ov.settle(ticket.token, ticket.payload)
    with pytest.raises(UnknownTokenError):
        ov.settle("no-such-token", {})


# ----------------------------------------------------------------- damaged or forged records


def add_key_row(deployment, key, item_id, seed):
    with closing(open_planted_db(deployment.planted_db)) as db:
        db.execute(
            "INSERT INTO submission_keys (item_id, queue_id, submission_key, seed) "
            "VALUES (?, ?, ?, ?)",
            (item_id, QUEUE, key, seed),
        )


def test_a_forged_key_row_pointing_at_another_item_is_caught(ov, deployment, writes):
    """Someone with write access to planted.db files an item under a new key."""
    tickets = ov.submit(proposal(1))
    ticket = next(t for t in tickets if t.payload == proposal(1).payload)
    decide(ov, ticket)
    item_id = deployment.query("SELECT item_id FROM events WHERE type = 'decided'")[0]["item_id"]
    add_key_row(deployment, "forged", item_id, b"s" * 32)
    with pytest.raises(GuardError, match="Nothing was executed"):
        ov.execute_call("forged", ticket.payload, writes)
    assert writes.done == []


def test_a_second_real_item_under_a_key_stops_everything(ov, deployment, writes):
    key, submitted, real, _ = keyed_pair(ov)
    decide(ov, real)
    ov.submit(proposal(50), key="other")
    with (
        closing(open_planted_db(deployment.planted_db)) as db,
        triggers_removed(db, "submission_keys"),
    ):
        db.execute(
            "UPDATE submission_keys SET submission_key = ? WHERE submission_key = 'other'",
            (key,),
        )
    with pytest.raises(GuardError, match="Nothing was executed"):
        ov.execute_call(key, submitted.payload, writes)
    with pytest.raises(StoreError, match="rp verify"):
        ov.call_status(key)
    assert writes.done == []


def test_a_planted_item_relabelled_as_real_does_not_run_through_a_key(ov, deployment, writes):
    key, _, _, planted = keyed_pair(ov)
    decide(ov, planted)
    with (
        closing(open_planted_db(deployment.planted_db)) as db,
        triggers_removed(db, "item_registry"),
    ):
        db.execute(
            "UPDATE item_registry SET kind = 'real', planted_error_id = NULL, "
            "assigned_reviewer = NULL WHERE kind = 'planted'"
        )
    with pytest.raises(GuardError, match="Nothing was executed"):
        ov.execute_call(key, planted.payload, writes)
    assert writes.done == []


def test_swapping_which_item_of_a_pair_is_real_never_reports_the_planted_decision(
    ov, deployment, writes
):
    """Exactly one item still says "real", so only the log's commitments can show the swap."""
    key, _, _, planted = keyed_pair(ov)
    decide(ov, planted)
    sql = "SELECT item_id, kind, planted_error_id FROM item_registry WHERE item_id IN "
    sql += "(SELECT item_id FROM submission_keys WHERE submission_key = ?)"
    rows = {r["kind"]: r for r in deployment.query(sql, (key,), planted=True)}
    update = "UPDATE item_registry SET kind = ?, planted_error_id = ?, assigned_reviewer = NULL "
    update += "WHERE item_id = ?"
    with (
        closing(open_planted_db(deployment.planted_db)) as db,
        triggers_removed(db, "item_registry"),
    ):
        wrong_error = rows["planted"]["planted_error_id"]
        db.execute(update, ("planted", wrong_error, rows["real"]["item_id"]))
        db.execute(update, ("real", None, rows["planted"]["item_id"]))
    with pytest.raises(StoreError, match="rp verify"):
        ov.call_status(key)
    with pytest.raises(GuardError, match="Nothing was executed"):
        ov.execute_call(key, planted.payload, writes)
    assert writes.done == []


def test_a_second_decision_in_the_log_stops_the_call(ov, deployment, writes):
    key, submitted, real, _ = keyed_pair(ov)
    decide(ov, real, "reject")
    with ov._log.transaction():
        found = ov._records.find(real.token)
        ov._records.append(EventType.DECIDED, found, "r-2", Decision.APPROVE)
    with pytest.raises(StoreError, match="inconsistent"):
        ov.call_status(key)
    with pytest.raises(OversightError):
        ov.execute_call(key, submitted.payload, writes)
    assert writes.done == []


def test_with_the_registry_down_a_resume_runs_nothing(ov, deployment, writes):
    key, submitted, real, _ = keyed_pair(ov)
    decide(ov, real)
    ov._planted.close()
    with pytest.raises(GuardError, match="Nothing was executed"):
        ov.execute_call(key, submitted.payload, writes)
    assert writes.done == []


def test_a_decision_forged_in_the_log_does_not_wake_a_call(ov, deployment, writes):
    key, submitted, real, _ = keyed_pair(ov)
    decide(ov, real, "reject")
    with closing(open_oversight_db(deployment.db)) as db, triggers_removed(db):
        db.execute("UPDATE events SET decision = 'approve' WHERE type = 'decided'")
    with pytest.raises(OversightError):
        ov.execute_call(key, submitted.payload, writes)
    assert writes.done == []
