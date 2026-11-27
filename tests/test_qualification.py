"""Qualification mode (step 3.1)."""

import copy
import json
from collections import Counter
from contextlib import closing

import pytest
import yaml
from deploy import QUEUE, item
from helpers import triggers_removed
from qual_support import correct_items, is_correct, load_pool, plan, sit

from reviewproof import OversightError, TransitionError, UnknownTokenError
from reviewproof.metrics.projection import load
from reviewproof.models import DefectClass, EventType, QualificationStatus
from reviewproof.qualification import Criteria, PlanError, load_plan
from reviewproof.store import StoreError, open_oversight_db, open_planted_db
from reviewproof.store.eventlog import EventLog


@pytest.fixture
def quiet(make):
    """A queue where live planted errors are practically never due."""
    return make(rate=0.001)


@pytest.fixture
def ov(quiet):
    return quiet.open()


@pytest.fixture
def pool(ov):
    return load_pool(ov, range(12))


@pytest.fixture
def q(ov):
    return ov.qualification()


def tamper(deployment, table, statement, arguments=()):
    with (
        closing(open_planted_db(deployment.planted_db)) as db,
        triggers_removed(db, table),
    ):
        db.execute(statement, arguments)


# ----------------------------------------------------------------- passing and failing


def test_a_careful_reviewer_passes(q, pool, quiet):
    session = q.start("r-1", plan(pool))
    assert len(session.items) == 20
    result = sit(q, session)
    assert result.passed and not result.abandoned
    assert (result.caught, result.defects, result.false_rejects, result.correct_items) == (
        12,
        12,
        0,
        8,
    )
    assert result.by_class == {
        DefectClass.WRONG_CLASSIFICATION: (4, 4),
        DefectClass.POLICY_BREACH: (4, 4),
        DefectClass.OMISSION: (4, 4),
    }
    [row] = quiet.query("SELECT * FROM qualifications")
    assert (row["score"], row["passed"], row["queue_id"]) == (1.0, 1, QUEUE)
    assert quiet.verify().ok, quiet.verify().registry_errors


@pytest.mark.req("REQ-031")
@pytest.mark.parametrize(
    ("label", "catch", "reject_correct"),
    [
        ("rejects everything", lambda p: True, lambda p: True),
        ("approves everything", lambda p: False, lambda p: False),
    ],
)
def test_answering_everything_the_same_way_fails(q, pool, label, catch, reject_correct):
    weakest = plan(pool, min_caught=1, max_false_rejects=4)  # the loosest mark allowed
    result = sit(q, q.start("r-1", weakest), catch, reject_correct)
    assert not result.passed, label


@pytest.mark.parametrize(("misses", "passed"), [(2, True), (3, False)])
def test_the_catch_mark(q, pool, misses, passed):
    skipped = set()

    def catch(payload):
        if len(skipped) < misses:
            skipped.add(payload["deviation_id"])
        return payload["deviation_id"] not in skipped

    assert sit(q, q.start("r-1", plan(pool)), catch).passed is passed


@pytest.mark.parametrize(("wrong", "passed"), [(1, True), (2, False)])
def test_the_false_rejection_mark(q, pool, wrong, passed):
    rejected = []

    def reject_correct(payload):
        rejected.append(payload)
        return len(rejected) <= wrong

    result = sit(q, q.start("r-1", plan(pool)), reject_correct=reject_correct)
    assert (result.false_rejects, result.passed) == (wrong, passed)


def test_edits_are_scored_like_live_ones(q, pool):
    session = q.start("r-1", plan(pool, min_caught=1))
    fixed_field = other_field = 0
    for entry in session.items:
        payload = copy.deepcopy(entry.payload)
        if is_correct(payload):
            payload["description"] += " (changed)"  # changing correct work counts against
            q.answered(entry.token, "r-1", "edit", payload)
        elif fixed_field == 0:
            payload["classification"] = "critical"
            payload["capa_required"] = not payload["capa_required"]
            payload["immediate_actions"] = [*payload["immediate_actions"], "added"]
            fixed_field += 1  # touches every possible defect field
            q.answered(entry.token, "r-1", "edit", payload)
        else:
            payload["description"] += " (looked at)"
            other_field += 1
            q.answered(entry.token, "r-1", "edit", payload)
    result = q.finish(session.session_id)
    assert (result.caught, result.false_rejects, result.passed) == (1, 8, False)


# ----------------------------------------------------------------- the items


def test_nothing_tells_which_items_are_defective(q, pool, quiet):
    session = q.start("r-1", plan(pool))
    order = [is_correct(t.payload) for t in session.items]
    assert len({len(t.token) for t in session.items}) == 1
    assert len({tuple(t.payload) for t in session.items}) <= 1
    assert order != sorted(order) and order != sorted(order, reverse=True)
    assert "DEV-" not in repr(session) and session.items[0].token not in repr(session)
    [event] = quiet.query("SELECT data FROM events WHERE type = 'qualification_started'")
    data = event["data"]
    assert "Q-0" not in data and "planted" not in data
    entries = json.loads(data)["items"]
    assert {tuple(sorted(e)) for e in entries} == {("commitment", "item_id")}
    assert len({e["commitment"] for e in entries}) == 20


@pytest.mark.req("REQ-007", "REQ-032")
def test_the_two_pools_never_mix(make):
    deployment = make(rate=0.5)
    ov = deployment.open()
    qualification_ids = load_pool(ov, range(12))
    live_ids = load_pool(ov, range(20, 24), purpose="live")
    for number in range(30):
        for ticket in ov.submit(item(number % 20)):
            ov.shown(ticket.token, "r-9")
            ov.decided(ticket.token, "r-9", "approve")
    used = {
        row["planted_error_id"]
        for row in deployment.query(
            "SELECT planted_error_id FROM item_registry WHERE kind = 'planted'", planted=True
        )
    }
    assert used and used <= set(live_ids)
    mixed = plan([*qualification_ids[:11], live_ids[0]])
    with pytest.raises(PlanError, match="not approved qualification planted errors.*L-020"):
        ov.qualification().start("r-1", mixed)


@pytest.mark.req("REQ-032")
def test_a_session_never_executes_or_enters_the_metrics(q, pool, quiet):
    sit(q, q.start("r-1", plan(pool)))
    assert load(quiet.db, quiet.planted_db).records == ()
    types = Counter(r["type"] for r in quiet.query("SELECT type FROM events"))
    assert types["qualification_answered"] == 20
    assert not {"submitted", "decided", "execute_requested"} & set(types)
    assert quiet.query("SELECT * FROM items") == []
    assert quiet.query("SELECT * FROM item_registry", planted=True) == []


def test_used_qualification_planted_errors_cannot_gain_approvals(ov, pool, q):
    from reviewproof.planted import ApprovalError

    q.start("r-1", plan(pool))
    with pytest.raises(ApprovalError, match="has been used"):
        ov.planted.approve(pool[0], "qa-3")


# ----------------------------------------------------------------- the plan


@pytest.mark.req("REQ-031")
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"queue": "other"}, "the plan is for queue"),
        ({"correct_items": correct_items(4)}, "at least 5 correct items"),
        ({"criteria": Criteria(0, 0)}, "min_caught must be from 1 to 12"),
        ({"criteria": Criteria(13, 0)}, "min_caught must be from 1 to 12"),
        ({"criteria": Criteria(10, 5)}, "from 0 to 4"),
        ({"criteria": Criteria(10, -1)}, "from 0 to 4"),
        ({"criteria": Criteria(10.0, 1)}, "whole number"),
        ({"criteria": Criteria(True, 1)}, "whole number"),
        ({"correct_items": (*correct_items(7), {"x": 1})}, r"correct_items\[7\]: missing"),
        ({"correct_items": (*correct_items(7), *correct_items(1))}, "same as another"),
        ({"correct_items": (*correct_items(7), {"x": float("nan")})}, "only JSON"),
        (
            {
                "correct_items": (
                    *correct_items(7),
                    {**correct_items(1)[0], "root_cause": "rp-reserved-1"},
                )
            },
            "reserved target prefix",
        ),
    ],
)
def test_bad_plans(q, pool, quiet, changes, message):
    with pytest.raises(PlanError, match=message):
        q.start("r-1", plan(pool, **changes))
    assert quiet.query("SELECT * FROM qualification_items", planted=True) == []
    assert quiet.query("SELECT 1 AS x FROM events WHERE type LIKE 'qualification%'") == []


@pytest.mark.req("REQ-031")
def test_a_session_needs_ten_defects_and_no_repeats_in_the_list(q, pool):
    with pytest.raises(PlanError, match="at least 10 planted errors.*has 9"):
        q.start("r-1", plan(pool[:9]))
    with pytest.raises(PlanError, match="listed twice"):
        q.start("r-1", plan([*pool[:11], pool[0]]))


def test_unapproved_and_retired_planted_errors_are_refused(ov, q, pool):
    ov.planted.retire(pool[0], "qa-1")
    with pytest.raises(PlanError, match=pool[0]):
        q.start("r-1", plan(pool))


def test_a_correct_item_equal_to_a_defect_is_refused(ov, q, pool):
    twin = dict(ov.planted.get(pool[0]).payload)
    with pytest.raises(PlanError, match="same as another"):
        q.start("r-1", plan(pool, correct_items=(*correct_items(7), twin)))


def test_an_unusable_schema(quiet, q, pool):
    (quiet.folder / "item.schema.json").write_text('{"oneOf": []}', encoding="utf-8")
    with pytest.raises(PlanError, match="schema cannot be used"):
        q.start("r-1", plan(pool))


def write_plan(folder, ids, **changes):
    (folder / "correct.json").write_text(json.dumps(list(correct_items(8))), encoding="utf-8")
    data = {
        "queue": QUEUE,
        "planted_errors": list(ids),
        "correct_items": "correct.json",
        "pass": {"min_caught": 10, "max_false_rejects": 1},
        **changes,
    }
    path = folder / "plan.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_load_a_plan(tmp_path):
    loaded = load_plan(write_plan(tmp_path, ["Q-1"]))
    assert loaded.criteria == Criteria(10, 1)
    assert len(loaded.correct_items) == 8


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"extra": 1}, "unknown key"),
        ({"correct_items": []}, "must name a file"),
        ({"correct_items": "absent.json"}, "cannot read"),
        ({"pass": 5}, "must hold min_caught"),
        ({"pass": {"min_caught": 1}}, "missing key"),
        ({"planted_errors": "Q-1"}, "list of planted error IDs"),
        ({"planted_errors": [1]}, "list of planted error IDs"),
    ],
)
def test_bad_plan_files(tmp_path, changes, message):
    with pytest.raises(PlanError, match=message):
        load_plan(write_plan(tmp_path, ["Q-1"], **changes))


@pytest.mark.parametrize(("name", "text"), [("p.yaml", "- 1"), ("p.yaml", "queue: [")])
def test_plan_files_that_are_not_plans(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")
    with pytest.raises(PlanError):
        load_plan(tmp_path / name)


def test_the_correct_items_file_must_be_a_list(tmp_path):
    path = write_plan(tmp_path, ["Q-1"])
    (tmp_path / "correct.json").write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(PlanError, match="list of items"):
        load_plan(path)


# ----------------------------------------------------------------- fixed in advance


@pytest.mark.req("REQ-031")
def test_the_pass_mark_cannot_change_mid_session(q, ov, pool, quiet, tmp_path):
    path = write_plan(tmp_path, pool, **{"pass": {"min_caught": 12, "max_false_rejects": 0}})
    session = q.start("r-1", load_plan(path))
    write_plan(tmp_path, pool, **{"pass": {"min_caught": 1, "max_false_rejects": 4}})
    first = []

    def miss_one(payload):
        first.append(payload)
        return len(first) > 1

    result = sit(q, session, miss_one)
    assert result.criteria == Criteria(12, 0) and not result.passed


@pytest.mark.req("REQ-033")
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE qualification_items SET planted_error_id = NULL WHERE planted_error_id = ?",
        "UPDATE qualification_items SET payload = '{}' WHERE planted_error_id = ?",
        "UPDATE qualification_items SET position = 99 WHERE planted_error_id = ?",
        "DELETE FROM qualification_items WHERE planted_error_id = ?",
    ],
)
def test_a_changed_answer_key_is_detected(q, pool, quiet, statement):
    session = q.start("r-1", plan(pool))
    for entry in session.items:
        q.answered(entry.token, "r-1", "approve")
    tamper(quiet, "qualification_items", statement, (pool[0],))
    with pytest.raises((StoreError, UnknownTokenError)):
        q.finish(session.session_id)
    errors = quiet.verify().registry_errors
    assert len(errors) == 1 and "session records do not match" in errors[0]


def test_a_changed_planted_error_is_detected(q, pool, quiet):
    sit(q, q.start("r-1", plan(pool)))
    tamper(
        quiet,
        "planted_errors",
        "UPDATE planted_errors SET defect_field = 'description', expected_decision = "
        "'reject' WHERE planted_error_id = ?",
        (pool[0],),
    )
    assert "session records do not match" in quiet.verify().registry_errors[0]


def forge(deployment, event_type, **data):
    with closing(open_oversight_db(deployment.db)) as db:
        system = db.execute("SELECT system_id FROM systems").fetchone()[0]
        EventLog(db).append(
            event_type,
            system_id=system,
            queue_id=QUEUE,
            item_id=data.pop("item_id", None),
            reviewer_id=data.pop("reviewer_id", "r-1"),
            data=data,
        )


@pytest.mark.req("REQ-033")
def test_a_result_that_the_answers_do_not_support_is_detected(q, pool, quiet):
    session = q.start("r-1", plan(pool))
    for entry in session.items:
        q.answered(entry.token, "r-1", "approve")  # misses everything
    forge(
        quiet,
        EventType.QUALIFICATION_FINISHED,
        session_id=session.session_id,
        passed=True,
        abandoned=False,
        caught=12,
        defects=12,
        false_rejects=0,
        correct_items=8,
    )
    assert "result does not match the answers" in quiet.verify().registry_errors[0]


def test_a_result_with_unanswered_items_is_detected(q, pool, quiet):
    session = q.start("r-1", plan(pool))
    forge(
        quiet,
        EventType.QUALIFICATION_FINISHED,
        session_id=session.session_id,
        passed=False,
        abandoned=False,
        caught=0,
        defects=12,
        false_rejects=0,
        correct_items=8,
    )
    assert "result does not match" in quiet.verify().registry_errors[0]


@pytest.mark.parametrize(
    "extra",
    [
        {"item_id": "not-in-session"},
        {"reviewer_id": "someone-else"},
    ],
)
def test_foreign_entries_in_a_session_are_detected(q, pool, quiet, extra):
    session = q.start("r-1", plan(pool))
    forge(quiet, EventType.QUALIFICATION_ANSWERED, session_id=session.session_id, **extra)
    assert "do not belong" in quiet.verify().registry_errors[0]


def test_a_second_answer_forged_into_the_log_is_detected(q, pool, quiet):
    session = q.start("r-1", plan(pool))
    q.answered(session.items[0].token, "r-1", "approve")
    [row] = quiet.query("SELECT item_id FROM events WHERE type = 'qualification_answered'")
    forge(
        quiet,
        EventType.QUALIFICATION_ANSWERED,
        session_id=session.session_id,
        item_id=row["item_id"],
    )
    assert "answered twice" in quiet.verify().registry_errors[0]


def test_a_session_without_a_start_or_with_bad_data_is_reported(q, pool, quiet):
    forge(quiet, EventType.QUALIFICATION_SHOWN, session_id="ghost")
    forge(quiet, EventType.QUALIFICATION_STARTED, session_id="broken")
    errors = quiet.verify().registry_errors
    assert any("no start" in e for e in errors)
    assert any("do not match" in e for e in errors)


def test_unreadable_session_records_are_reported(q, pool, quiet):
    q.start("r-1", plan(pool))
    with closing(open_planted_db(quiet.planted_db)) as db:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("DROP TABLE qualification_items")
    assert "cannot be read" in quiet.verify().registry_errors[0]


# ----------------------------------------------------------------- one session at a time


@pytest.mark.req("REQ-032")
def test_one_open_session_and_new_items_for_a_retake(ov, q, pool):
    session = q.start("r-1", plan(pool))
    with pytest.raises(TransitionError, match="unfinished session"):
        q.start("r-1", plan(pool))
    q.start("r-2", plan(pool))  # someone else can sit the same items
    sit(q, session, lambda p: False)
    with pytest.raises(PlanError, match="met these in an earlier session"):
        q.start("r-1", plan(pool))
    fresh = load_pool(ov, range(40, 52))
    assert sit(q, q.start("r-1", plan(fresh))).passed


@pytest.mark.req("REQ-032")
def test_abandoning_counts_as_not_passed_and_uses_the_items(q, pool, quiet):
    session = q.start("r-1", plan(pool, min_caught=1, max_false_rejects=4))
    for entry in session.items:
        if not is_correct(entry.payload):
            q.answered(entry.token, "r-1", "reject")
    result = q.abandon(session.session_id)
    assert result.abandoned and not result.passed and result.caught == 12
    with pytest.raises(PlanError, match="earlier session"):
        q.start("r-1", plan(pool))
    assert quiet.verify().ok


def test_finishing_needs_every_answer(q, pool):
    session = q.start("r-1", plan(pool))
    q.answered(session.items[0].token, "r-1", "approve")
    with pytest.raises(TransitionError, match=r"19 item\(s\) have not been answered"):
        q.finish(session.session_id)


def test_answer_rules(q, pool):
    session = q.start("r-1", plan(pool))
    token = session.items[0].token
    with pytest.raises(TransitionError, match="another reviewer"):
        q.answered(token, "r-2", "approve")
    for bad in ("nope", None):
        with pytest.raises(UnknownTokenError):
            q.answered(bad, "r-1", "approve")
    with pytest.raises(OversightError, match="approve, edit or reject"):
        q.answered(token, "r-1", "maybe")
    with pytest.raises(OversightError, match="needs final_payload"):
        q.answered(token, "r-1", "edit")
    with pytest.raises(OversightError, match="only an edit"):
        q.answered(token, "r-1", "approve", {"a": 1})
    with pytest.raises(OversightError, match="unchanged"):
        q.answered(token, "r-1", "edit", session.items[0].payload)
    q.answered(token, "r-1", "approve")
    with pytest.raises(TransitionError, match="already been answered"):
        q.answered(token, "r-1", "reject")
    q.abandon(session.session_id)
    for attempt in (
        lambda: q.answered(session.items[1].token, "r-1", "approve"),
        lambda: q.shown(session.items[1].token, "r-1"),
        lambda: q.finish(session.session_id),
        lambda: q.abandon(session.session_id),
    ):
        with pytest.raises(TransitionError, match="already ended"):
            attempt()
    with pytest.raises(UnknownTokenError, match="no session"):
        q.finish("nope")


# ----------------------------------------------------------------- the reviewer's own view


def test_a_judgment_comes_before_the_item_is_shown(q, pool, quiet):
    session = q.start("r-1", plan(pool))
    first, second = session.items[0].token, session.items[1].token
    q.judged(first, "r-1", {"classification": "major"})
    q.shown(first, "r-1")
    q.answered(first, "r-1", "approve")
    with pytest.raises(TransitionError, match="must come first"):
        q.judged(first, "r-1", {"classification": "minor"})
    q.shown(second, "r-1")
    with pytest.raises(TransitionError, match="must come first"):
        q.judged(second, "r-1", {})
    [row] = quiet.query("SELECT judgment FROM qualification_judgments", planted=True)
    assert json.loads(row["judgment"]) == {"classification": "major"}
    dump = json.dumps(quiet.query("SELECT * FROM events"))
    assert '"classification": "major"' not in dump
    assert quiet.verify().ok


@pytest.mark.req("REQ-033")
@pytest.mark.parametrize(
    "statement",
    ["UPDATE qualification_judgments SET judgment = '{}'", "DELETE FROM qualification_judgments"],
)
def test_a_changed_judgment_is_detected(q, pool, quiet, statement):
    session = q.start("r-1", plan(pool))
    q.judged(session.items[0].token, "r-1", {"classification": "major"})
    tamper(quiet, "qualification_judgments", statement)
    assert "do not match" in quiet.verify().registry_errors[0]


def test_session_records_are_permanent(q, pool, quiet):
    import sqlite3

    session = q.start("r-1", plan(pool))
    q.judged(session.items[0].token, "r-1", {})
    with closing(open_planted_db(quiet.planted_db)) as db:
        for table in ("qualification_items", "qualification_judgments"):
            for statement in (f"UPDATE {table} SET item_id = item_id", f"DELETE FROM {table}"):
                with pytest.raises(sqlite3.IntegrityError, match=table):
                    db.execute(statement)
        with pytest.raises(sqlite3.IntegrityError, match="content cannot be changed"):
            db.execute("UPDATE planted_errors SET purpose = 'live'")


# ----------------------------------------------------------------- who is qualified


def state(q, reviewer="r-1"):
    return q.status(reviewer).status


@pytest.mark.req("REQ-034")
def test_status_follows_the_ai_system_version(ov, q, pool, quiet):
    assert state(q) is QualificationStatus.NOT_QUALIFIED
    result = sit(q, q.start("r-1", plan(pool)))
    current = q.status("r-1")
    assert current.status is QualificationStatus.QUALIFIED
    assert current.session_id == result.session_id and current.passed_at
    assert state(q, "r-2") is QualificationStatus.NOT_QUALIFIED
    [row] = quiet.query("SELECT qualification_status FROM reviewers WHERE reviewer_id = 'r-1'")
    assert row["qualification_status"] == "qualified"

    assert ov.register_system(prompt_version="2") == ["r-1"]
    assert state(q) is QualificationStatus.REQUALIFICATION_REQUIRED
    fresh = load_pool(ov, range(40, 52))
    sit(q, q.start("r-1", plan(fresh)))
    assert state(q) is QualificationStatus.QUALIFIED

    ov.register_system(prompt_version="1")  # going back is also a change
    assert state(q) is QualificationStatus.REQUALIFICATION_REQUIRED


@pytest.mark.req("REQ-034")
def test_a_change_during_a_session_means_it_does_not_count(ov, q, pool):
    session = q.start("r-1", plan(pool))
    ov.register_system(model_version="2.0")
    result = sit(q, session)
    assert result.passed  # the answers met the mark...
    assert state(q) is QualificationStatus.REQUALIFICATION_REQUIRED  # ...for the old version


def test_a_later_failure_does_not_remove_an_earlier_pass(ov, q, pool):
    sit(q, q.start("r-1", plan(pool)))
    fresh = load_pool(ov, range(40, 52))
    sit(q, q.start("r-1", plan(fresh)), lambda p: False)
    assert state(q) is QualificationStatus.QUALIFIED


def test_status_does_not_trust_the_summary_table(q, pool, quiet):
    sit(q, q.start("r-1", plan(pool)), lambda p: False)
    with closing(open_oversight_db(quiet.db)) as db:
        db.execute("UPDATE qualifications SET passed = 1, score = 1.0")
    assert state(q) is QualificationStatus.NOT_QUALIFIED


def test_status_refuses_an_edited_log(q, pool, quiet):
    sit(q, q.start("r-1", plan(pool)), lambda p: False)
    with closing(open_oversight_db(quiet.db)) as db, triggers_removed(db):
        db.execute(
            "UPDATE events SET data = replace(data, '\"passed\":false', '\"passed\":true') "
            "WHERE type = 'qualification_finished'"
        )
    with pytest.raises(StoreError, match="does not match its hash"):
        q.status("r-1")


# ----------------------------------------------------------------- failures


@pytest.mark.parametrize("table", ["qualification_items", "qualification_judgments"])
def test_a_failing_restricted_database_records_nothing(quiet, q, pool, table):
    session = q.start("r-0", plan(pool))
    with closing(open_planted_db(quiet.planted_db)) as db:
        db.execute(
            f"CREATE TRIGGER stop BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'full'); END"
        )
    before = quiet.query("SELECT COUNT(*) AS n FROM events")
    with pytest.raises(StoreError, match="full"):
        if table == "qualification_items":
            q.start("r-1", plan(pool))
        else:
            q.judged(session.items[0].token, "r-0", {})
    assert quiet.query("SELECT COUNT(*) AS n FROM events") == before


def test_a_damaged_item_is_a_store_error(quiet, q, pool):
    session = q.start("r-1", plan(pool))
    tamper(quiet, "qualification_items", "UPDATE qualification_items SET payload = 'x'")
    with pytest.raises(StoreError, match="damaged"):
        q.answered(session.items[0].token, "r-1", "approve")


@pytest.mark.parametrize("action", ["answer", "start again"])
def test_a_missing_table_is_a_store_error(quiet, q, pool, action):
    session = q.start("r-1", plan(pool))
    q.abandon(session.session_id)
    with closing(open_planted_db(quiet.planted_db)) as db:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("DROP TABLE qualification_items")
    with pytest.raises(StoreError, match="cannot read"):
        if action == "answer":
            q.answered(session.items[0].token, "r-1", "approve")
        else:
            q.start("r-1", plan(pool))
