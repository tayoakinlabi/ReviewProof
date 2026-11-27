"""Model and prompt changes (step 1.10)."""

import dataclasses
import json
from contextlib import closing

import pytest
from deploy import QUEUE, item

from reviewproof import Oversight, OversightError
from reviewproof.store import open_oversight_db

SYSTEM = "deviation-classifier"


def review(ov, reviewer, number=0):
    for ticket in ov.submit(item(number)):
        ov.shown(ticket.token, reviewer)
        ov.decided(ticket.token, reviewer, "approve")


def statuses(deployment):
    rows = deployment.query("SELECT reviewer_id, qualification_status FROM reviewers")
    return {r["reviewer_id"]: r["qualification_status"] for r in rows}


def contexts(deployment):
    rows = deployment.query(
        "SELECT data FROM events WHERE type = 'context_registered' ORDER BY event_id"
    )
    return [json.loads(r["data"]) for r in rows]


def changed_config(config, **changes):
    context = dataclasses.replace(config.systems[SYSTEM], **changes)
    return dataclasses.replace(config, systems={SYSTEM: context})


@pytest.fixture
def worked(make):
    deployment = make(rate=0.001)
    ov = deployment.open()
    review(ov, "r-1")
    review(ov, "r-2", 1)
    return deployment, ov


@pytest.mark.req("REQ-030")
def test_a_prompt_version_bump_flags_the_systems_reviewers(worked):
    deployment, ov = worked
    assert set(statuses(deployment).values()) == {"not_qualified"}
    assert ov.register_system(prompt_version="2") == ["r-1", "r-2"]
    assert set(statuses(deployment).values()) == {"requalification_required"}
    first, second = contexts(deployment)
    assert first["previous_system_id"] is None
    assert second["requalification_required"] == ["r-1", "r-2"]
    assert second["prompt_version"] == "2"
    assert second["previous_system_id"]
    assert len(deployment.query("SELECT * FROM systems")) == 2


@pytest.mark.req("REQ-030")
@pytest.mark.parametrize(
    "change",
    [{"model_id": "other"}, {"model_version": "2.0"}, {"tool_schema_hash": "new"}],
)
def test_each_identifying_change_counts(worked, change):
    _, ov = worked
    assert ov.register_system(**change) == ["r-1", "r-2"]


@pytest.mark.req("REQ-030")
def test_new_items_get_the_new_context_and_old_ones_keep_theirs(worked):
    deployment, ov = worked
    [old] = ov.submit(item(2))
    ov.register_system(prompt_version="2")
    [new] = ov.submit(item(3))
    for ticket in (old, new):
        ov.shown(ticket.token, "r-3")
    shown = deployment.query(
        "SELECT system_id FROM events WHERE type = 'shown' AND reviewer_id = 'r-3' "
        "ORDER BY event_id"
    )
    assert shown[0]["system_id"] != shown[1]["system_id"]
    [queue] = deployment.query("SELECT system_id FROM queues")
    assert queue["system_id"] == shown[1]["system_id"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({}, "nothing that identifies"),
        ({"intended_use": "reworded"}, "nothing that identifies"),
        ({"prompt_version": "1"}, "nothing that identifies"),
        ({"model_version": " "}, "non-empty"),
        ({"model_version": 2}, "non-empty"),
    ],
)
def test_a_change_must_change_something_valid(worked, change, message):
    deployment, ov = worked
    with pytest.raises(OversightError, match=message):
        ov.register_system(**change)
    assert len(contexts(deployment)) == 1
    assert set(statuses(deployment).values()) == {"not_qualified"}


def test_changing_the_settings_file_flags_reviewers_on_next_open(worked, caplog):
    deployment, ov = worked
    ov.close()
    deployment.open(config=changed_config(deployment.config, model_version="2.0"))
    assert set(statuses(deployment).values()) == {"requalification_required"}
    assert "2 reviewer(s) need requalification" in caplog.text


@pytest.mark.req("REQ-030")
def test_going_back_to_an_earlier_context_is_also_a_change(worked):
    deployment, ov = worked
    ov.register_system(prompt_version="2")
    review(ov, "r-3", 4)
    assert ov.register_system(prompt_version="1") == ["r-3"]
    assert len(deployment.query("SELECT * FROM systems")) == 2
    assert len(contexts(deployment)) == 3


def test_reopening_unchanged_records_nothing_and_flags_nobody(worked):
    deployment, ov = worked
    deployment.open()
    deployment.open()
    assert len(contexts(deployment)) == 1
    assert set(statuses(deployment).values()) == {"not_qualified"}


def test_a_second_queue_on_the_same_context_flags_nobody(worked):
    deployment, _ = worked
    queue = deployment.config.queues[QUEUE]
    both = dataclasses.replace(
        deployment.config,
        queues={QUEUE: queue, "second": dataclasses.replace(queue, name="second")},
    )
    Oversight(both, "second", deployment.db, deployment.planted_db).close()
    assert set(statuses(deployment).values()) == {"not_qualified"}


def test_people_with_only_a_qualification_are_flagged_too(worked):
    deployment, ov = worked
    with closing(open_oversight_db(deployment.db)) as db:
        db.execute("INSERT INTO reviewers VALUES ('r-9', NULL, 'qualified')")
        system_id = db.execute("SELECT system_id FROM systems").fetchone()[0]
        db.execute(
            "INSERT INTO qualifications VALUES ('q-1', 'r-9', ?, ?, 1.0, 1, "
            "'2026-11-26T10:00:00.000000Z')",
            (system_id, QUEUE),
        )
    assert "r-9" in ov.register_system(prompt_version="2")
    assert statuses(deployment)["r-9"] == "requalification_required"


def test_another_systems_reviewers_are_left_alone(worked):
    deployment, ov = worked
    base = deployment.config.systems[SYSTEM]
    queue = deployment.config.queues[QUEUE]
    both = dataclasses.replace(
        deployment.config,
        systems={SYSTEM: base, "other": dataclasses.replace(base, system="other")},
        queues={
            QUEUE: queue,
            "other-q": dataclasses.replace(queue, name="other-q", system="other"),
        },
    )
    with Oversight(both, "other-q", deployment.db, deployment.planted_db) as other:
        review(other, "r-other", 5)
    assert ov.register_system(prompt_version="2") == ["r-1", "r-2"]
    assert statuses(deployment)["r-other"] == "not_qualified"
