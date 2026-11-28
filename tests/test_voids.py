"""Voiding a faulty planted error (ADR-008): who may, how it is recorded and checked, and
what follows for feedback, qualification and reports.
"""

import csv
import dataclasses
import io
import json
import logging
import sqlite3
from contextlib import closing

import pytest
from deploy import QUEUE, item
from helpers import triggers_removed
from qual_support import defect_input, load_pool, plan, sit

from reviewproof import GuardError, Oversight, OversightError
from reviewproof.cli.main import main
from reviewproof.metrics.projection import EvidenceError, load
from reviewproof.models import EventType, QualificationStatus, VoidReason
from reviewproof.report import build, html, tables
from reviewproof.store import StoreError, open_oversight_db, open_planted_db
from reviewproof.store.eventlog import EventLog, IntegrityError, verify
from reviewproof.store.void_check import commitment_for, read_voids
from reviewproof.voids import VoidError, VoidState

#: Words that must never leave the restricted database.
EXPLANATION = "SECRET-EXPLANATION-the-item-was-right"
REFERENCE = "SECRET-REFERENCE-DEV-77"


def number_of(payload):
    """Which planted error a ticket is, from the identifier its content carries. None if real."""
    prefix, _, digits = payload["deviation_id"].rpartition("-")
    return int(digits) if prefix == "DEV-2024" else None


class History:
    """A deployment with live reviews. Planted errors L-100 on, approved by qa-1 and qa-2,
    written by author-1. Even-numbered ones are caught, odd-numbered ones missed.
    """

    def __init__(self, make, items=40, reviewers=("r-1", "r-2"), pool=range(100, 130)):
        self.deployment = make(rate=0.5)
        self.ov = self.deployment.open()
        load_pool(self.ov, pool, purpose="live")
        self.missed, self.caught, self.tickets = [], [], {}
        for n in range(items):
            reviewer = reviewers[n % len(reviewers)]
            for ticket in self.ov.submit(item(n % 20, deviation_id=f"DEV-2025-{n:04d}")):
                number = number_of(ticket.payload)
                catch = number is not None and number % 2 == 0
                self.ov.shown(ticket.token, reviewer)
                self.ov.decided(ticket.token, reviewer, "reject" if catch else "approve")
                if number is not None:
                    name = f"L-{number:03d}"
                    (self.caught if catch else self.missed).append(name)
                    self.tickets[name] = ticket

    def void(self, planted_error_id, requester="qa-1", confirmer="qa-lead"):
        voids = self.ov.voids()
        voids.request(planted_error_id, requester, "not_defective", EXPLANATION, REFERENCE)
        return voids.confirm(planted_error_id, confirmer)

    def report(self, **arguments):
        projection = load(self.deployment.db, self.deployment.planted_db)
        return build(projection, self.deployment.config, self.deployment.planted_db, **arguments)

    def planted_events(self):
        return self.deployment.query(
            "SELECT * FROM events WHERE type = 'planted_voided' ORDER BY event_id"
        )


@pytest.fixture
def history(make):
    return History(make)


def catch_counts(report):
    rate = report.queues[0].summary.catch
    return rate.successes, rate.total


# ----------------------------------------------------------------- who may void


@pytest.mark.req("REQ-054")
def test_a_request_changes_nothing(history):
    before = history.report()
    target = history.missed[0]
    history.ov.voids().request(target, "qa-1", "not_defective", EXPLANATION, REFERENCE)
    assert history.planted_events() == []
    assert history.ov.planted.get(target).retired_at is None
    after = history.report()
    assert catch_counts(after) == catch_counts(before) and after.queues[0].voided is None
    assert [state for _, state in history.ov.voids().all()] == [VoidState.AWAITING_CONFIRMATION]


@pytest.mark.req("REQ-054")
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"reason": "recognisable"}, "reason must be one of"),
        ({"reason": "too_hard"}, "reason must be one of"),
        ({"explanation": "  "}, "explanation must not be empty"),
        ({"reference": ""}, "reference must not be empty"),
        ({"requested_by": " ​ "}, "requested_by must be a non-empty name"),
        ({"planted_error_id": "L-999"}, "no planted error"),
    ],
)
def test_a_request_needs_a_listed_reason_an_explanation_and_a_reference(history, changes, message):
    arguments = {
        "planted_error_id": history.missed[0],
        "requested_by": "qa-1",
        "reason": "not_defective",
        "explanation": EXPLANATION,
        "reference": REFERENCE,
        **changes,
    }
    with pytest.raises(VoidError, match=message):
        history.ov.voids().request(**arguments)
    assert history.ov.voids().all() == []


@pytest.mark.req("REQ-054")
def test_the_database_takes_each_listed_reason(history):
    voids = history.ov.voids()
    for number, reason in enumerate(VoidReason):
        voids.request(history.missed[0], f"qa-{number + 5}", reason, EXPLANATION, REFERENCE)
        voids.cancel(history.missed[0], "qa-lead")
    assert [void.reason for void, _ in voids.all()] == list(VoidReason)


@pytest.mark.req("REQ-054")
def test_one_never_released_or_sat_cannot_be_voided_only_retired(history):
    unused = history.ov.planted.import_data(defect_input(200, "live")).planted_error_id
    with pytest.raises(VoidError, match="never been released.*retire it instead"):
        history.ov.voids().request(unused, "qa-1", "not_defective", EXPLANATION, REFERENCE)


@pytest.mark.req("REQ-054")
@pytest.mark.parametrize(
    ("requester", "confirmer", "message"),
    [
        ("qa-lead", "qa-lead", "a different person must confirm"),
        ("qa-lead", " QA-Lead ", "a different person must confirm"),
        ("author-1", "qa-1", "neither wrote nor approved"),
        ("qa-1", "qa-2", "neither wrote nor approved"),
        ("QA-2", "Author-1", "neither wrote nor approved"),
    ],
)
def test_two_different_people_one_of_them_independent(history, requester, confirmer, message):
    target = history.missed[0]
    voids = history.ov.voids()
    voids.request(target, requester, "not_defective", EXPLANATION, REFERENCE)
    with pytest.raises(VoidError, match=message):
        voids.confirm(target, confirmer)
    assert history.planted_events() == [] and voids.in_effect() == {}
    assert history.ov.planted.get(target).retired_at is None


@pytest.mark.req("REQ-054")
@pytest.mark.parametrize(
    ("requester", "confirmer"),
    [("qa-1", "qa-lead"), ("qa-lead", "author-1"), ("qa-8", "qa-9")],
)
def test_one_maker_with_an_independent_person_or_two_of_them_can_void(
    history, requester, confirmer
):
    target = history.missed[0]
    history.void(target, requester, confirmer)
    assert list(history.ov.voids().in_effect()) == [target]


@pytest.mark.req("REQ-054")
@pytest.mark.parametrize("reviewer", ["r-1", " R-1 "])
def test_a_reviewer_who_met_it_cannot_ask(history, reviewer):
    target = next(
        name
        for name in history.missed
        if history.deployment.query(
            "SELECT 1 FROM events WHERE type = 'decided' AND reviewer_id = 'r-1' AND item_id "
            "IN (SELECT value FROM json_each(?))",
            (json.dumps(items_of(history, name)),),
        )
    )
    with pytest.raises(VoidError, match="recorded as a reviewer"):
        history.ov.voids().request(target, reviewer, "not_defective", EXPLANATION, REFERENCE)


def items_of(history, planted_error_id):
    rows = history.deployment.query(
        "SELECT item_id FROM item_registry WHERE planted_error_id = ?",
        (planted_error_id,),
        planted=True,
    )
    return [row["item_id"] for row in rows]


@pytest.mark.req("REQ-054")
def test_someone_who_is_shown_it_after_asking_cannot_confirm(history):
    # A copy is released and not yet shown.
    for n in range(40, 80):
        tickets = history.ov.submit(item(n % 20, deviation_id=f"DEV-2025-{n:04d}"))
        waiting = [t for t in tickets if number_of(t.payload) is not None]
        if waiting:
            break
    target = f"L-{number_of(waiting[0].payload):03d}"
    voids = history.ov.voids()
    voids.request(target, "qa-8", "not_defective", EXPLANATION, REFERENCE)
    history.ov.shown(waiting[0].token, "qa-9")
    with pytest.raises(VoidError, match="qa-9 is recorded as a reviewer"):
        voids.confirm(target, "qa-9")


@pytest.mark.req("REQ-054")
def test_requests_can_be_cancelled_and_a_void_is_permanent(history):
    target = history.missed[0]
    voids = history.ov.voids()
    with pytest.raises(VoidError, match="no request to confirm"):
        voids.confirm(target, "qa-lead")
    voids.request(target, "qa-1", "not_defective", EXPLANATION, REFERENCE)
    with pytest.raises(VoidError, match="already a request"):
        voids.request(target, "qa-8", "not_findable", EXPLANATION, REFERENCE)
    voids.cancel(target, "qa-8")
    with pytest.raises(VoidError, match="no request"):
        voids.confirm(target, "qa-lead")
    history.void(target)
    assert [state for _, state in voids.all()] == [VoidState.CANCELLED, VoidState.IN_EFFECT]
    for step in (
        lambda: voids.cancel(target, "qa-8"),
        lambda: voids.confirm(target, "qa-9"),
        lambda: voids.request(target, "qa-8", "not_findable", EXPLANATION, REFERENCE),
    ):
        with pytest.raises(VoidError, match="already voided.*permanent"):
            step()
    assert len(history.planted_events()) == 1


@pytest.mark.req("REQ-054")
def test_confirming_retires_it_and_keeps_an_earlier_retirement(history):
    first, second = history.missed[0], history.caught[0]
    history.void(first)
    retired = history.ov.planted.get(first)
    assert (retired.retired_by, retired.retire_reason) == ("qa-lead", "voided: not_defective")
    history.ov.planted.retire(second, "qa-5", "spotted")
    history.void(second)
    kept = history.ov.planted.get(second)
    assert (kept.retired_by, kept.retire_reason) == ("qa-5", "spotted")


@pytest.mark.req("REQ-054")
def test_only_its_own_queue_can_void_it(history):
    queue = history.deployment.config.queues[QUEUE]
    other = dataclasses.replace(queue, name="other-queue")
    config = dataclasses.replace(
        history.deployment.config, queues={QUEUE: queue, "other-queue": other}
    )
    with (
        Oversight(
            config, "other-queue", history.deployment.db, history.deployment.planted_db
        ) as elsewhere,
        pytest.raises(VoidError, match=f"belongs to queue '{QUEUE}'"),
    ):
        elsewhere.voids().request(
            history.missed[0], "qa-1", "not_defective", EXPLANATION, REFERENCE
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE void_requests SET reason = 'not_findable'",
        "DELETE FROM void_requests",
        "UPDATE void_confirmations SET confirmed_by = 'someone'",
        "DELETE FROM void_confirmations",
        "INSERT INTO void_cancellations VALUES ((SELECT void_id FROM void_requests), '  ', "
        "'2026-01-01T00:00:00.000000Z')",
    ],
)
def test_the_database_refuses_to_change_void_records(history, statement):
    history.void(history.missed[0])
    with (
        closing(open_planted_db(history.deployment.planted_db)) as db,
        pytest.raises(sqlite3.IntegrityError),
    ):
        db.execute(statement)


def test_the_database_refuses_the_requesters_own_confirmation(history):
    history.ov.voids().request(
        history.missed[0], "qa-lead", "not_defective", EXPLANATION, REFERENCE
    )
    with (
        closing(open_planted_db(history.deployment.planted_db)) as db,
        pytest.raises(sqlite3.IntegrityError, match="different person"),
    ):
        db.execute(
            "INSERT INTO void_confirmations SELECT void_id, ' QA-LEAD ', requested_at "
            "FROM void_requests"
        )


@pytest.mark.req("REQ-054")
@pytest.mark.parametrize(("requester", "confirmer"), [("author-1", "qa-1"), ("QA-1", " qa-2")])
def test_the_database_also_requires_one_of_the_two_to_be_independent(history, requester, confirmer):
    history.ov.voids().request(
        history.missed[0], requester, "not_defective", EXPLANATION, REFERENCE
    )
    with (
        closing(open_planted_db(history.deployment.planted_db)) as db,
        pytest.raises(sqlite3.IntegrityError, match="independent"),
    ):
        db.execute(
            "INSERT INTO void_confirmations SELECT void_id, ?, requested_at FROM void_requests",
            (confirmer,),
        )


# ----------------------------------------------------------------- the record


@pytest.mark.req("REQ-055")
def test_the_log_holds_only_a_commitment(history):
    target = history.missed[0]
    history.void(target)
    [event] = history.planted_events()
    assert (event["item_id"], event["reviewer_id"], event["decision"]) == (None, None, None)
    assert event["queue_id"] == QUEUE
    assert list(json.loads(event["data"])) == ["commitment"]
    history.deployment.close()
    stored = b"".join(
        path.read_bytes() for path in history.deployment.db.parent.glob("oversight.db*")
    )
    for secret in (target, EXPLANATION, REFERENCE):
        assert secret.encode() not in stored


@pytest.mark.req("REQ-055")
@pytest.mark.req("REQ-012")
@pytest.mark.req("REQ-013")
@pytest.mark.req("REQ-056")
def test_voiding_while_a_copy_waits_gives_nothing_away_and_still_blocks_it(history, caplog):
    for n in range(40, 80):
        tickets = history.ov.submit(item(n % 20, deviation_id=f"DEV-2025-{n:04d}"))
        if len(tickets) == 2:
            break
    planted = next(t for t in tickets if number_of(t.payload) is not None)
    target = f"L-{number_of(planted.payload):03d}"
    real = next(t for t in tickets if t is not planted)
    with caplog.at_level(logging.DEBUG):
        history.void(target, "qa-8", "qa-9")
        written = []
        for ticket in (real, planted):
            assert history.ov.shown(ticket.token, "r-1") is None
            assert history.ov.decided(ticket.token, "r-1", "approve") is None
        waiting = history.deployment.query(
            "SELECT type, data FROM events WHERE item_id IN (SELECT item_id FROM events "
            "ORDER BY event_id DESC LIMIT 4)"
        )
        assert not any("planted" in row["data"] for row in waiting)
        for ticket in (real, planted):
            history.ov.execute(ticket.token, ticket.payload, lambda *c: written.append(c))
    assert len(written) == 1  # the real one only
    assert target not in caplog.text and EXPLANATION not in caplog.text


def tamper(history, table, statement):
    with (
        closing(open_planted_db(history.deployment.planted_db)) as db,
        triggers_removed(db, table),
    ):
        assert db.execute(statement).rowcount >= 1


@pytest.mark.req("REQ-055")
@pytest.mark.parametrize(
    ("table", "statement"),
    [
        ("void_requests", "UPDATE void_requests SET reason = 'not_findable'"),
        ("void_requests", "UPDATE void_requests SET explanation = 'a better story'"),
        ("void_requests", "UPDATE void_requests SET reference = 'DEV-1'"),
        ("void_requests", "UPDATE void_requests SET requested_by = 'someone'"),
        ("void_confirmations", "UPDATE void_confirmations SET confirmed_by = 'someone'"),
        (
            "void_confirmations",
            "UPDATE void_confirmations SET confirmed_at = '2020-01-01T00:00:00.000000Z'",
        ),
        ("void_confirmations", "DELETE FROM void_confirmations"),
        (
            "void_cancellations",
            "INSERT INTO void_cancellations SELECT void_id, 'x', requested_at FROM void_requests",
        ),
        (
            "planted_errors",
            "UPDATE planted_errors SET author = 'someone' WHERE planted_error_id IN "
            "(SELECT planted_error_id FROM void_requests)",
        ),
    ],
)
def test_a_void_that_was_changed_or_removed_gives_no_scores(history, table, statement):
    history.void(history.missed[0])
    assert history.deployment.verify().ok
    tamper(history, table, statement)
    result = history.deployment.verify()
    assert any("void record does not match" in e for e in result.registry_errors), result
    assert not any(history.missed[0] in e for e in result.registry_errors)
    with pytest.raises(EvidenceError):
        history.report()
    # Code that acts on voids without verifying the whole log refuses too.
    for act in (
        history.ov.voids().in_effect,
        lambda: history.ov.feedback("r-1", batch=True),
        lambda: history.ov.qualification().status("r-1"),
    ):
        with pytest.raises(IntegrityError, match="void records do not match"):
            act()


@pytest.mark.req("REQ-055")
def test_a_second_log_entry_cannot_share_a_record(history):
    history.void(history.missed[0])
    [event] = history.planted_events()
    with closing(open_oversight_db(history.deployment.db)) as db:
        EventLog(db).append(
            EventType.PLANTED_VOIDED,
            system_id=event["system_id"],
            queue_id=QUEUE,
            data=json.loads(event["data"]),
        )
    assert not history.deployment.verify().ok


@pytest.mark.req("REQ-055")
def test_a_log_entry_for_a_record_never_confirmed_is_refused(history):
    history.ov.voids().request(history.missed[0], "qa-1", "not_defective", EXPLANATION, REFERENCE)
    [system] = history.deployment.query("SELECT DISTINCT system_id FROM events")
    with closing(open_oversight_db(history.deployment.db)) as db:
        EventLog(db).append(
            EventType.PLANTED_VOIDED,
            system_id=system["system_id"],
            queue_id=QUEUE,
            data={"commitment": "0" * 64},
        )
    assert not history.deployment.verify().ok


@pytest.mark.req("REQ-055")
@pytest.mark.parametrize("wrong", [{"queue_id": "another"}, {"item_id": "x"}, {"reviewer_id": "x"}])
def test_an_entry_for_the_right_record_must_name_its_queue_and_nothing_else(
    history, monkeypatch, wrong
):
    with monkeypatch.context() as broken:
        failing_log(history, broken)
        with pytest.raises(StoreError):
            history.void(history.missed[0])
    with closing(open_planted_db(history.deployment.planted_db)) as planted:
        [void] = read_voids(planted)
        commitment = commitment_for(planted, void)
    [system] = history.deployment.query("SELECT DISTINCT system_id FROM events")
    with closing(open_oversight_db(history.deployment.db)) as db:
        EventLog(db).append(
            EventType.PLANTED_VOIDED,
            system_id=system["system_id"],
            data={"commitment": commitment},
            **{"queue_id": QUEUE, **wrong},
        )
    result = history.deployment.verify()
    assert any("void record does not match" in e for e in result.registry_errors), result


@pytest.mark.req("REQ-055")
def test_cutting_the_void_off_the_log_is_found_with_an_exported_head(history):
    history.void(history.missed[0])
    head = history.deployment.verify().head
    with (
        closing(open_oversight_db(history.deployment.db)) as db,
        triggers_removed(db),
    ):
        db.execute("DELETE FROM events WHERE type = 'planted_voided'")
    with (
        closing(open_oversight_db(history.deployment.db)) as db,
        closing(open_planted_db(history.deployment.planted_db)) as planted,
    ):
        assert verify(db, planted).ok  # the chain alone cannot see it
        assert verify(db, planted, head).anchor_error


def failing_log(history, monkeypatch):
    append = history.ov._log.append

    def fail_on_voids(event_type, **arguments):
        if event_type is EventType.PLANTED_VOIDED:
            raise StoreError("the disk is full")
        return append(event_type, **arguments)

    monkeypatch.setattr(history.ov._log, "append", fail_on_voids)


@pytest.mark.req("REQ-055")
def test_an_interrupted_void_has_no_effect_and_can_be_completed(history, monkeypatch):
    target = history.missed[0]
    before = catch_counts(history.report())
    with monkeypatch.context() as broken:
        failing_log(history, broken)
        with pytest.raises(StoreError, match="disk is full"):
            history.void(target)
    voids = history.ov.voids()
    assert [state for _, state in voids.all()] == [VoidState.NOT_COMPLETED]
    assert history.deployment.verify().ok and catch_counts(history.report()) == before
    with pytest.raises(VoidError, match="they must confirm again"):
        voids.confirm(target, "qa-9")
    voids.confirm(target, "qa-lead")
    assert catch_counts(history.report()) != before and history.deployment.verify().ok
    assert len(history.planted_events()) == 1


@pytest.mark.req("REQ-055")
def test_an_interrupted_void_can_be_cancelled_instead(history, monkeypatch):
    target = history.missed[0]
    with monkeypatch.context() as broken:
        failing_log(history, broken)
        with pytest.raises(StoreError):
            history.void(target)
    history.ov.voids().cancel(target, "qa-8")
    assert [state for _, state in history.ov.voids().all()] == [VoidState.CANCELLED]
    assert history.deployment.verify().ok
    assert history.ov.planted.get(target).retired_at  # it stays out of use


def edit_records(history, *statements):
    """Change the restricted database as someone who removes its protections could."""
    with closing(sqlite3.connect(history.deployment.planted_db, isolation_level=None)) as db:
        db.execute("PRAGMA ignore_check_constraints = ON")
        triggers = db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
        for (name,) in triggers:
            if name.startswith("void_"):
                db.execute(f"DROP TRIGGER {name}")
        for statement in statements:
            db.execute(statement)


@pytest.mark.req("REQ-055")
@pytest.mark.parametrize(
    ("statements", "message"),
    [
        # As if planted.db had been restored from a copy made before the upgrade.
        (
            [
                "DROP TABLE void_confirmations",
                "DROP TABLE void_cancellations",
                "DROP TABLE void_requests",
            ],
            "void record does not match",
        ),
        (["DROP TABLE void_cancellations"], "void records cannot be read"),
        (["UPDATE void_requests SET reason = 'nonsense'"], "void records cannot be read"),
        (["UPDATE void_requests SET salt = x'00'"], "void record does not match"),
    ],
    ids=["tables gone", "a table gone", "unknown reason", "short salt"],
)
def test_lost_or_damaged_void_records_never_bring_results_back_unnoticed(
    history, statements, message
):
    history.void(history.missed[0])
    edit_records(history, *statements)
    result = history.deployment.verify()
    assert any(message in error for error in result.registry_errors), result.registry_errors
    with pytest.raises(EvidenceError):
        history.report()


# ----------------------------------------------------------------- reports and feedback


@pytest.mark.req("REQ-056")
@pytest.mark.req("REQ-057")
def test_a_report_leaves_the_results_out_and_shows_them_beside(history):
    before = history.report()
    target = history.missed[0]
    copies = len(items_of(history, target))
    history.void(target)
    report = history.report(individuals=True)
    queue = report.queues[0]

    caught, total = catch_counts(before)
    left = queue.voided.results
    assert 1 <= left <= copies
    assert catch_counts(report) == (caught, total - left)
    assert (queue.voided.left_out.successes, queue.voided.left_out.total) == (0, left)
    assert (queue.voided.with_them.successes, queue.voided.with_them.total) == (caught, total)
    assert queue.summary.decided == before.queues[0].summary.decided
    assert queue.summary.fast == before.queues[0].summary.fast

    page = html.render(report)
    words = " ".join(page.split())
    assert f"Leaves out {left} voided result(s)" in words
    assert f"Left out: 0 catch(es) and {left} miss(es)." in words
    assert "Catch rate with them:" in words and "Without them:" in words
    assert "Planted errors by reason: the item was correct: 1." in words
    assert "Voided planted error</dt>" in page
    sheet = tables.render(report)
    rows = list(csv.DictReader(io.StringIO(sheet)))
    found = {(r["scope_type"], r["metric"]): r for r in rows}
    assert found["queue", "catch_rate"]["note"] == f"leaves out {left} voided result(s)"
    assert found["queue", "catch_rate_with_voided"]["total"] == str(total)
    assert found["queue", "voided_results_caught"]["successes"] == "0"
    assert found["queue", "voided_reason_not_defective"]["value"] == "1"
    assert found["system_version", "catch_rate_with_voided"]["total"] == str(total)
    for secret in (target, EXPLANATION, REFERENCE, "DEV-2024"):
        assert secret not in page and secret not in sheet

    # Every report made after it, whatever period it covers.
    later = history.report(since=None)
    assert later.queues[0].voided == history.report().queues[0].voided


@pytest.mark.req("REQ-057")
def test_individual_results_show_both(make):
    history = History(make, items=60, reviewers=("r-1",))
    target = history.missed[0]
    history.void(target)
    report = history.report(individuals=True)
    [person] = report.queues[0].reviewers
    assert person.voided.with_them.total == person.summary.catch.total + person.voided.results
    assert "With 1 voided:" in " ".join(html.render(report).split())
    assert any(
        r["scope_type"] == "reviewer" and r["metric"] == "catch_rate_with_voided"
        for r in csv.DictReader(io.StringIO(tables.render(report)))
    )


@pytest.mark.req("REQ-057")
@pytest.mark.req("REQ-040")
def test_a_person_with_too_few_left_shows_nothing_about_what_was_voided(make):
    history = History(make, items=8, reviewers=("r-1",))
    assert len(history.missed) + len(history.caught) < 10
    history.void(history.missed[0])
    report = history.report(individuals=True)
    [person] = report.queues[0].reviewers
    assert person.summary is None and person.voided.results == 1
    assert person.voided.left_out.withheld and person.voided.with_them.withheld
    assert report.queues[0].voided.with_them.withheld
    page = " ".join(html.render(report).split())
    assert "are withheld: too few for one person alone" in page
    assert "Catch rate with them" not in page


@pytest.mark.req("REQ-057")
def test_group_rows_show_the_figure_with_voided_results_too(history):
    history.void(history.missed[0])
    report = history.report()
    for group in (*report.by_version, *report.by_site):
        assert group.voided.results >= 1
        assert f"With {group.voided.results} voided:" in html._group_row(group)


@pytest.mark.req("REQ-057")
def test_alerts_tell_the_qa_lead_when_too_much_is_voided(history, capsys):
    for target in sorted(set(history.missed))[:4]:
        history.void(target)
    settings = str(history.deployment.folder / "reviewproof.yaml")
    files = ["--db", str(history.deployment.db), "--planted", str(history.deployment.planted_db)]
    assert main(["alerts", "--config", settings, *files]) == 0
    printed = capsys.readouterr().out
    assert "Voided results" in printed and "planted-error results are voided" in printed
    assert not any(target in printed for target in history.missed)


@pytest.mark.req("REQ-042")
def test_no_feedback_is_given_about_a_voided_planted_error(history):
    decided = history.deployment.query(
        "SELECT item_id FROM events WHERE type = 'decided' AND reviewer_id = 'r-1'"
    )
    mine = {row["item_id"] for row in decided}
    target = next(n for n in history.missed if mine & set(items_of(history, n)))
    expected = len(
        history.deployment.query(
            "SELECT item_id FROM item_registry WHERE kind = 'planted' AND item_id IN "
            "(SELECT value FROM json_each(?)) AND planted_error_id <> ?",
            (json.dumps(sorted(mine)), target),
            planted=True,
        )
    )
    history.void(target)
    sent = history.ov.feedback("r-1", batch=True)
    assert len(sent) == expected > 0
    assert all(number_of(f.payload) != int(target[2:]) for f in sent)
    assert history.ov.feedback("r-1", batch=True) == []


def test_reading_voids_changes_nothing_and_a_report_without_voids_is_as_before(history):
    report = history.report(individuals=True)
    page = html.render(report)
    assert report.queues[0].voided is None
    assert "oided" not in page.split("Limits used")[0].replace("voided results at most", "")


# ----------------------------------------------------------------- qualification


@pytest.fixture
def quiet(make):
    """A queue where live planted errors are practically never due."""
    deployment = make(rate=0.001)
    ov = deployment.open()
    return deployment, ov, load_pool(ov, range(30))


def take(ov, pool, reviewer="r-1", missed=(), mark=9):
    """A session of 10 planted errors, missing those at the given places in ``pool``."""
    chosen = pool[:10]
    wrong = {f"DEV-2024-{int(pool[i][2:]):04d}" for i in missed}
    qualification = ov.qualification()
    session = qualification.start(reviewer, plan(chosen, min_caught=mark))
    return sit(qualification, session, catch=lambda p: p["deviation_id"] not in wrong)


def void_in_pool(ov, planted_error_id):
    voids = ov.voids()
    voids.request(planted_error_id, "qa-1", "not_defective", EXPLANATION, REFERENCE)
    voids.confirm(planted_error_id, "qa-lead")


@pytest.mark.req("REQ-058")
@pytest.mark.parametrize(
    ("missed", "mark", "voided", "keeps", "caught"),
    [
        ((), 9, 0, True, 9),  # 10 catches: 9 on the 9 that count
        ((5,), 9, 0, False, 8),  # 9 catches, including the voided one
        ((0,), 9, 0, True, 9),  # missed only the voided one
        ((), 10, 0, False, 9),  # a mark of every planted error leaves no room
    ],
)
def test_a_pass_counts_only_while_its_mark_is_met_without_voided_ones(
    quiet, missed, mark, voided, keeps, caught
):
    deployment, ov, pool = quiet
    result = take(ov, pool, missed=missed, mark=mark)
    assert result.passed
    stored = deployment.query("SELECT * FROM qualifications")
    void_in_pool(ov, pool[voided])
    state = ov.qualification().status("r-1")
    expected = (
        QualificationStatus.QUALIFIED if keeps else QualificationStatus.REQUALIFICATION_REQUIRED
    )
    assert state.status is expected
    assert (state.voided, state.still_counting, state.still_caught, state.mark) == (
        1,
        9,
        caught,
        mark,
    )
    assert state.affected == (result.session_id,)
    [reviewer] = deployment.query("SELECT * FROM reviewers WHERE reviewer_id = 'r-1'")
    assert reviewer["qualification_status"] == expected.value
    # The record of the session does not change, and still verifies.
    assert deployment.query("SELECT * FROM qualifications") == stored
    assert deployment.verify().ok, deployment.verify().registry_errors


@pytest.mark.req("REQ-058")
def test_a_fail_never_becomes_a_pass(quiet):
    deployment, ov, pool = quiet
    result = take(ov, pool, missed=(0, 1), mark=9)
    assert not result.passed
    for planted_error_id in pool[:2]:  # both of the ones missed
        void_in_pool(ov, planted_error_id)
    state = ov.qualification().status("r-1")
    assert state.status is QualificationStatus.NOT_QUALIFIED
    assert state.affected == (result.session_id,) and state.session_id is None
    assert deployment.query("SELECT passed FROM qualifications") == [{"passed": 0}]


@pytest.mark.req("REQ-058")
def test_an_earlier_pass_that_is_untouched_still_counts(quiet):
    _, ov, pool = quiet
    first = take(ov, pool[10:20] + pool[:10], mark=9)
    second = take(ov, pool, mark=10)
    assert ov.qualification().status("r-1").session_id == second.session_id
    void_in_pool(ov, pool[0])
    state = ov.qualification().status("r-1")
    assert state.status is QualificationStatus.QUALIFIED
    assert state.session_id == first.session_id and state.voided == 0
    assert state.affected == (second.session_id,)


@pytest.mark.req("REQ-058")
def test_a_planted_error_voided_while_its_session_is_open(quiet):
    deployment, ov, pool = quiet
    qualification = ov.qualification()
    session = qualification.start("r-1", plan(pool[:10], min_caught=10))
    void_in_pool(ov, pool[0])
    result = sit(qualification, session)
    assert result.passed and result.caught == 10  # recorded as it was taken
    state = qualification.status("r-1")
    assert state.status is QualificationStatus.REQUALIFICATION_REQUIRED
    [reviewer] = deployment.query("SELECT * FROM reviewers WHERE reviewer_id = 'r-1'")
    assert reviewer["qualification_status"] != "qualified"
    assert deployment.verify().ok


@pytest.mark.req("REQ-058")
def test_relabelling_a_session_item_cannot_hide_a_void_from_qualification_status(quiet):
    deployment, ov, pool = quiet
    take(ov, pool, mark=10)
    void_in_pool(ov, pool[0])
    with (
        closing(open_planted_db(deployment.planted_db)) as db,
        triggers_removed(db, "qualification_items"),
    ):
        # Point the voided item at an unused planted error, so nothing looks voided.
        db.execute(
            "UPDATE qualification_items SET planted_error_id = ? WHERE planted_error_id = ?",
            (pool[29], pool[0]),
        )
    with pytest.raises(StoreError, match="do not match the log"):
        ov.qualification().status("r-1")


@pytest.mark.req("REQ-054")
def test_whoever_sat_it_cannot_void_it(quiet):
    _, ov, pool = quiet
    take(ov, pool, reviewer="r-7")
    with pytest.raises(VoidError, match="recorded as a reviewer"):
        ov.voids().request(pool[0], "r-7", "not_defective", EXPLANATION, REFERENCE)


# ----------------------------------------------------------------- the commands


@pytest.mark.req("REQ-054")
def test_the_commands(quiet, capsys):
    deployment, ov, pool = quiet
    take(ov, pool)  # all 10 caught, against a mark of 9
    target = pool[0]
    settings = str(deployment.folder / "reviewproof.yaml")
    files = ["--db", str(deployment.db), "--planted", str(deployment.planted_db)]
    plant = ["--config", settings, *files]
    why = ["--reason", "wrong_expected_decision", "--explanation", "x", "--reference", "y"]

    def run(*arguments, expect=0):
        status = main(list(arguments))
        captured = capsys.readouterr()
        assert status == expect, captured
        return captured.out + captured.err

    def listed():
        output = run("plant", "list", *plant)
        return next(line for line in output.splitlines() if line.startswith(target + " "))

    assert "Nothing has changed" in run("plant", "void", target, *plant, "--by", "qa-1", *why)
    assert "[void awaiting confirmation]" in listed()
    assert "different person" in run(
        "plant", "void-confirm", target, *plant, "--by", "qa-1", expect=2
    )
    assert "cancelled" in run("plant", "void-cancel", target, *plant, "--by", "qa-1")
    assert "void" not in listed().lower()
    run("plant", "void", target, *plant, "--by", "qa-1", *why)
    assert "voided" in run("plant", "void-confirm", target, *plant, "--by", "qa-lead")
    assert "[VOIDED]" in listed()
    assert "Voids in effect: 1." in run("verify", *files)
    status = run("qualify", "status", *plant, "--queue", QUEUE, "--reviewer", "r-1")
    assert "requalification required" not in status
    assert "1 planted error(s) in that session have since been voided" in status
    run("plant", "void", pool[1], *plant, "--by", "qa-1", *why)
    run("plant", "void-confirm", pool[1], *plant, "--by", "qa-lead")
    status = run("qualify", "status", *plant, "--queue", QUEUE, "--reviewer", "r-1")
    assert "requalification required" in status and "no longer counts" in status
    assert "retire it instead" in run(
        "plant", "void", pool[29], *plant, "--by", "qa-1", *why, expect=2
    )


@pytest.mark.req("REQ-054")
def test_the_reasons_are_the_four_in_adr_008():
    assert [reason.value for reason in VoidReason] == [
        "not_defective",
        "more_than_one_defect",
        "wrong_expected_decision",
        "not_findable",
    ]
    assert issubclass(VoidError, OversightError) and not issubclass(VoidError, GuardError)


def test_a_commitment_covers_every_part_of_the_record(history):
    history.void(history.missed[0])
    with closing(open_planted_db(history.deployment.planted_db)) as planted:
        [void] = read_voids(planted)
        original = commitment_for(planted, void)
        for name in (
            "planted_error_id",
            "explanation",
            "reference",
            "requested_by",
            "requested_at",
            "confirmed_by",
            "confirmed_at",
            "void_id",
        ):
            changed = history.caught[0] if name == "planted_error_id" else "changed"
            other = dataclasses.replace(void, **{name: changed})
            assert commitment_for(planted, other) != original, name
        assert commitment_for(planted, dataclasses.replace(void, salt=bytes(32))) != original
        reason = dataclasses.replace(void, reason=VoidReason.NOT_FINDABLE)
        assert commitment_for(planted, reason) != original
