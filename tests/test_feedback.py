"""Scoring planted errors and telling reviewers (part of step 1.9)."""

import dataclasses
from contextlib import closing

import pytest
from deploy import QUEUE, submit_until_pair
from helpers import triggers_removed

from reviewproof import Outcome, OversightError
from reviewproof.feedback import score
from reviewproof.models import FeedbackTiming
from reviewproof.store import StoreError, open_planted_db


@pytest.mark.parametrize(
    ("decision", "edited", "expected"),
    [
        ("reject", [], Outcome.CATCH),
        ("edit", ["classification"], Outcome.CATCH),
        ("edit", ["description", "classification"], Outcome.CATCH),
        ("edit", ["description"], Outcome.MISS),
        ("edit", [], Outcome.MISS),
        ("approve", [], Outcome.MISS),
    ],
)
def test_scoring(decision, edited, expected):
    assert score(decision, edited, "classification") is expected


def immediate(deployment):
    queue = dataclasses.replace(deployment.config.queues[QUEUE], feedback=FeedbackTiming.IMMEDIATE)
    config = dataclasses.replace(deployment.config, queues={QUEUE: queue})
    ov = deployment.open(config=config)
    deployment.load_planted(ov)
    return ov


def decide(ov, ticket, decision="approve", reviewer="r-1", **arguments):
    ov.shown(ticket.token, reviewer)
    ov.decided(ticket.token, reviewer, decision, **arguments)


@pytest.mark.parametrize(("decision", "outcome"), [("approve", "miss"), ("reject", "catch")])
def test_feedback_on_a_planted_error_only_and_only_once(deployment, decision, outcome):
    ov = immediate(deployment)
    real, planted = submit_until_pair(ov)
    decide(ov, real, decision)
    assert ov.feedback("r-1") == []
    decide(ov, planted, decision)
    [note] = ov.feedback("r-1")
    assert note.outcome.value == outcome
    assert note.decision.value == decision
    assert note.payload == planted.payload
    assert note.defect_field in note.payload or note.defect_class.value == "omission"
    assert note.expected_decision.startswith(("reject", "edit:"))
    assert note.decided_at
    assert ov.feedback("r-1") == []
    sent = deployment.query("SELECT reviewer_id FROM events WHERE type = 'feedback_sent'")
    assert sent == [{"reviewer_id": "r-1"}]
    assert deployment.verify().ok


def test_an_edit_that_misses_the_defect_is_reported_as_a_miss(deployment):
    ov = immediate(deployment)
    _, planted = submit_until_pair(ov)
    error = next(
        p for p in ov.planted.all() if p.payload["deviation_id"] == planted.payload["deviation_id"]
    )
    other = "root_cause" if error.defect_field != "root_cause" else "description"
    final = {**planted.payload, other: planted.payload[other] + " (checked)"}
    decide(ov, planted, "edit", final_payload=final)
    [note] = ov.feedback("r-1")
    assert note.outcome is Outcome.MISS


def test_each_reviewer_gets_their_own_in_decision_order(deployment):
    ov = immediate(deployment)
    first = submit_until_pair(ov)
    decide(ov, first[1], reviewer="r-1")
    second = submit_until_pair(ov, start=8)
    decide(ov, second[1], reviewer="r-1", decision="reject")
    assert ov.feedback("r-2") == []
    assert [n.outcome.value for n in ov.feedback("r-1")] == ["miss", "catch"]


def test_feedback_is_the_same_whether_or_not_execute_was_called(deployment):
    ov = immediate(deployment)
    _, planted = submit_until_pair(ov)
    decide(ov, planted)
    ov.execute(planted.token, planted.payload, lambda *a: pytest.fail("must not run"))
    assert [n.outcome.value for n in ov.feedback("r-1")] == ["miss"]


def test_a_batched_queue_waits_for_the_batch(deployment, ov):
    _, planted = submit_until_pair(ov)
    decide(ov, planted)
    assert ov.feedback("r-1") == []
    assert len(ov.feedback("r-1", batch=True)) == 1


def test_no_planted_errors_means_no_feedback(deployment):
    assert deployment.open().feedback("r-1", batch=True) == []


def test_a_reviewer_id_is_checked(ov):
    with pytest.raises(OversightError):
        ov.feedback(" ", batch=True)


def test_feedback_is_chunked(deployment, ov, monkeypatch):
    monkeypatch.setattr("reviewproof.feedback._CHUNK", 1)
    for start in (0, 8):
        decide(ov, submit_until_pair(ov, start=start)[1])
    assert len(ov.feedback("r-1", batch=True)) == 2


@pytest.mark.parametrize("how", ["relabelled", "no table"])
def test_feedback_that_cannot_be_verified_is_not_delivered(deployment, ov, how):
    _, planted = submit_until_pair(ov)
    decide(ov, planted)
    with closing(open_planted_db(deployment.planted_db)) as db:
        if how == "no table":
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("DROP TABLE item_registry")
        else:
            with triggers_removed(db, "item_registry"):
                db.execute("UPDATE item_registry SET target_id = 'x' WHERE kind = 'planted'")
    with pytest.raises(StoreError):
        ov.feedback("r-1", batch=True)
    assert deployment.query("SELECT * FROM events WHERE type = 'feedback_sent'") == []
