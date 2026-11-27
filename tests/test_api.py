import copy
import dataclasses
import json
import logging
import sqlite3
import threading
from collections import Counter
from contextlib import closing

import pytest
from deploy import EXAMPLES, ITEMS, QUEUE, SCHEMA_ORDER, item, submit_until_pair
from helpers import triggers_removed

from reviewproof import (
    Decision,
    Item,
    Oversight,
    OversightError,
    Ticket,
    TransitionError,
    UnknownTokenError,
    initialise,
)
from reviewproof.store import StoreError, open_oversight_db, open_planted_db
from reviewproof.store.commitments import payload_hash
from reviewproof.strictload import load_file

# ----------------------------------------------------------------- the whole flow


def test_twenty_demo_items_flow_through(make):
    deployment = make(rate=0.2)
    ov = deployment.open()
    deployment.load_planted(ov)
    queue = []
    for number in range(len(ITEMS)):
        queue += ov.submit(item(number))
        while queue:
            ticket = queue.pop(0)
            ov.shown(ticket.token, "r-1")
            ov.decided(ticket.token, "r-1", "approve")

    kinds = Counter(deployment.kinds().values())
    assert kinds["real"] == 20
    assert 1 <= kinds["planted"] <= 12
    total = sum(kinds.values())
    events = Counter(r["type"] for r in deployment.query("SELECT type FROM events"))
    assert events == {
        "context_registered": 1,
        "submitted": total,
        "shown": total,
        "decided": total,
    }
    result = deployment.verify()
    assert result.ok, (result.chain_error, result.registry_errors)


def test_real_payloads_come_back_unchanged(deployment):
    ov = deployment.open()
    original = copy.deepcopy(ITEMS[0])
    [ticket] = ov.submit(item(0))
    assert ticket.payload == original
    assert list(ticket.payload) == SCHEMA_ORDER


@pytest.mark.req("REQ-010")
def test_the_target_id_is_kept_in_the_registry_only(deployment):
    deployment.open().submit(item(3))
    [row] = deployment.query("SELECT target_id, kind FROM item_registry", planted=True)
    assert row == {"target_id": "QMS-REC-0003", "kind": "real"}
    dump = deployment.query("SELECT * FROM items") + deployment.query("SELECT * FROM events")
    assert "QMS-REC-0003" not in json.dumps(dump)


@pytest.mark.req("REQ-023")
def test_no_item_content_or_token_is_stored(deployment):
    [ticket] = deployment.open().submit(item(0))
    with closing(sqlite3.connect(deployment.db)) as db:
        dump = "\n".join(db.iterdump())
    assert ticket.token not in dump
    assert ITEMS[0]["description"] not in dump
    assert ITEMS[0]["lot_number"] not in dump


# ----------------------------------------------------------------- no giveaways


@pytest.mark.req("REQ-011")
def test_field_order_does_not_show_which_item_is_planted(deployment, ov):
    # The host sends fields in its own order; planted items are stored with sorted keys.
    for number in range(20):
        payload = dict(reversed(list(ITEMS[number].items())))
        tickets = ov.submit(Item(payload=payload, target_id=f"rec-{number}"))
        for ticket in tickets:
            assert list(ticket.payload) == [k for k in SCHEMA_ORDER if k in ticket.payload]
        for ticket in tickets:
            ov.shown(ticket.token, "r-1")
            ov.decided(ticket.token, "r-1", "reject")
    assert "planted" in deployment.kinds().values()


def test_nested_and_unlisted_fields_are_ordered_by_name(deployment, tmp_path):
    schema = {
        "properties": {
            "b": {"type": "object", "properties": {"z": {}, "y": {}}},
            "a": {"type": "array", "items": {"properties": {"n": {}, "m": {}}}},
        }
    }
    (deployment.folder / "item.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    payload = {"d": 1, "c": 2, "a": [{"m": 1, "n": 2}], "b": {"x": 0, "y": 1, "z": 2}}
    [ticket] = deployment.open().submit(Item(payload=payload, target_id="rec"))
    assert json.dumps(ticket.payload) == (
        '{"b": {"z": 2, "y": 1, "x": 0}, "a": [{"n": 2, "m": 1}], "c": 2, "d": 1}'
    )


@pytest.mark.req("REQ-010")
def test_real_and_planted_tickets_have_the_same_shape(ov):
    real, planted = submit_until_pair(ov)
    assert type(real) is type(planted) is Ticket
    assert len(real.token) == len(planted.token)
    assert list(real.payload) == list(planted.payload)
    assert {k: type(v) for k, v in real.payload.items()} == {
        k: type(v) for k, v in planted.payload.items()
    }
    assert repr(real) == repr(planted)


@pytest.mark.req("REQ-012")
def test_real_and_planted_rows_and_events_have_the_same_shape(deployment, ov):
    submit_until_pair(ov)
    kinds = deployment.kinds()
    items = {r["item_id"]: r for r in deployment.query("SELECT * FROM items")}
    events = {
        r["item_id"]: r for r in deployment.query("SELECT * FROM events WHERE type = 'submitted'")
    }
    assert set(kinds.values()) == {"real", "planted"}
    shapes = set()
    for item_id in items:
        row, event = items[item_id], events[item_id]
        data = json.loads(event["data"])
        shapes.add(
            (
                len(item_id),
                tuple((k, v is None) for k, v in event.items()),
                tuple(sorted(data)),
                tuple(sorted(data["field_hashes"])),
                row["queue_id"],
                row["system_id"],
            )
        )
    assert len(shapes) == 1


def test_hashes_do_not_repeat_when_a_planted_error_is_reused(make):
    deployment = make()
    ov = deployment.open()
    planted = ov.planted.import_file(EXAMPLES[0])
    ov.planted.approve(planted.planted_error_id, "qa-1")
    ov.planted.approve(planted.planted_error_id, "qa-2")
    for number in range(8):
        for ticket in ov.submit(item(number)):
            ov.shown(ticket.token, f"r-{number}")
            ov.decided(ticket.token, f"r-{number}", "reject")
    assert list(deployment.kinds().values()).count("planted") >= 2
    seen = []
    for row in deployment.query("SELECT data FROM events WHERE type = 'submitted'"):
        data = json.loads(row["data"])
        seen += [data["payload_hash"], data["commitment"], *data["field_hashes"].values()]
    assert len(seen) == len(set(seen))


@pytest.mark.req("REQ-011")
def test_neither_order_gives_the_planted_item_away(make):
    deployment = make()
    ov = deployment.open(seed=5)
    deployment.load_planted(ov)
    first_ticket, first_row, pairs = Counter(), Counter(), 0
    for number in range(120):
        payload = {**ITEMS[number % 20], "deviation_id": f"DEV-2025-{number:04d}"}
        tickets = ov.submit(Item(payload=payload, target_id=f"rec-{number}"))
        if len(tickets) == 2:
            pairs += 1
            first_ticket[tickets[0].payload == payload] += 1
            kinds = deployment.kinds()
            last_two = deployment.query(
                "SELECT item_id FROM events WHERE type = 'submitted' ORDER BY event_id DESC LIMIT 2"
            )
            first_row[kinds[last_two[1]["item_id"]]] += 1
        for ticket in tickets:
            ov.shown(ticket.token, "r-1")
            ov.decided(ticket.token, "r-1", "approve")
    assert pairs > 60
    for counts in (first_ticket, first_row):
        assert len(counts) == 2
        assert min(counts.values()) > 0.3 * pairs


@pytest.mark.req("REQ-011")
def test_both_kinds_are_refused_for_the_same_reasons(ov):
    tickets = submit_until_pair(ov)
    messages = []
    for ticket in tickets:
        seen = []
        for attempt in (
            lambda t: ov.decided(t, "r-1", "approve"),
            lambda t: ov.decided(t, "r-1", "edit"),
            lambda t: ov.shown(t, " "),
        ):
            with pytest.raises(OversightError) as error:
                attempt(ticket.token)
            seen.append((type(error.value), str(error.value)))
        ov.shown(ticket.token, "r-1")
        with pytest.raises(OversightError) as error:
            ov.decided(ticket.token, "r-1", "edit", final_payload=ticket.payload)
        seen.append((type(error.value), str(error.value)))
        ov.decided(ticket.token, "r-1", "approve")
        with pytest.raises(TransitionError) as error:
            ov.shown(ticket.token, "r-1")
        seen.append(str(error.value))
        messages.append(seen)
    assert messages[0] == messages[1]


@pytest.mark.req("REQ-012")
def test_nothing_in_the_host_logs_names_a_planted_error(ov, caplog):
    caplog.set_level(logging.DEBUG)
    real, planted = submit_until_pair(ov)
    assert caplog.records == []


# ----------------------------------------------------------------- states


@pytest.fixture
def ticket(deployment):
    ov = deployment.open()
    [ticket] = ov.submit(item(0))
    return ov, ticket.token


def test_no_decision_before_it_is_shown(ticket):
    ov, token = ticket
    with pytest.raises(TransitionError, match="shown"):
        ov.decided(token, "r-1", "approve")


@pytest.mark.req("REQ-020")
def test_the_decider_must_have_been_shown_it(ticket):
    ov, token = ticket
    ov.shown(token, "r-1")
    with pytest.raises(TransitionError, match="this reviewer"):
        ov.decided(token, "r-2", "approve")


@pytest.mark.req("REQ-022")
def test_showing_twice_and_to_two_people_is_recorded(ticket, deployment):
    ov, token = ticket
    for reviewer in ("r-1", "r-1", "r-2"):
        ov.shown(token, reviewer)
    ov.decided(token, "r-2", Decision.REJECT)
    rows = deployment.query("SELECT reviewer_id, decision FROM events WHERE type != 'submitted'")
    assert [r["reviewer_id"] for r in rows][1:] == ["r-1", "r-1", "r-2", "r-2"]
    assert rows[-1]["decision"] == "reject"
    reviewers = deployment.query("SELECT reviewer_id FROM reviewers")
    assert sorted(r["reviewer_id"] for r in reviewers) == ["r-1", "r-2"]


@pytest.mark.req("REQ-020")
@pytest.mark.parametrize("second", ["approve", "reject"])
def test_only_one_decision(ticket, second):
    ov, token = ticket
    ov.shown(token, "r-1")
    ov.shown(token, "r-2")
    ov.decided(token, "r-1", "approve")
    for reviewer in ("r-1", "r-2"):
        with pytest.raises(TransitionError, match="already been decided"):
            ov.decided(token, reviewer, second)
    with pytest.raises(TransitionError):
        ov.shown(token, "r-3")


@pytest.mark.parametrize("token", ["nope", "", None, 5, "\ud800"])
def test_unknown_tokens(ticket, token):
    ov, _ = ticket
    with pytest.raises(UnknownTokenError):
        ov.shown(token, "r-1")
    with pytest.raises(UnknownTokenError):
        ov.decided(token, "r-1", "approve")


def test_the_error_does_not_repeat_the_token(ticket):
    ov, _ = ticket
    with pytest.raises(UnknownTokenError) as error:
        ov.shown("SECRET-TOKEN", "r-1")
    assert "SECRET-TOKEN" not in str(error.value)


def test_a_token_belongs_to_its_queue(deployment):
    [ticket] = deployment.open().submit(item(0))
    queue = deployment.config.queues[QUEUE]
    both = dataclasses.replace(
        deployment.config,
        queues={QUEUE: queue, "other": dataclasses.replace(queue, name="other")},
    )
    with (
        Oversight(both, "other", deployment.db, deployment.planted_db) as other,
        pytest.raises(UnknownTokenError),
    ):
        other.shown(ticket.token, "r-1")


@pytest.mark.req("REQ-020")
@pytest.mark.parametrize("reviewer", ["", " ", " r-1", None, 7])
def test_a_reviewer_needs_a_clean_id(ticket, reviewer):
    ov, token = ticket
    with pytest.raises(OversightError, match="reviewer"):
        ov.shown(token, reviewer)
    with pytest.raises(OversightError, match="reviewer"):
        ov.decided(token, reviewer, "approve")
    if reviewer is not None:
        with pytest.raises(OversightError, match="reviewer"):
            ov.submit(item(1), reviewer)


# ----------------------------------------------------------------- edits


def edit_of(ov, token, final, **arguments):
    ov.shown(token, "r-1")
    ov.decided(token, "r-1", "edit", final_payload=final, **arguments)


def last_event_data(deployment):
    rows = deployment.query("SELECT data FROM events ORDER BY event_id DESC LIMIT 1")
    return json.loads(rows[0]["data"])


@pytest.mark.req("REQ-021")
@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"classification": "major"}, ["classification"]),
        ({"classification": "major", "capa_required": True}, ["capa_required", "classification"]),
        ({"immediate_actions": ITEMS[0]["immediate_actions"][:2]}, ["immediate_actions"]),
        ({"extra": None}, ["extra"]),
    ],
)
def test_reviewproof_works_out_what_changed(ticket, deployment, change, expected):
    ov, token = ticket
    edit_of(ov, token, {**ITEMS[0], **change})
    assert last_event_data(deployment)["edited_fields"] == expected


@pytest.mark.req("REQ-021")
def test_a_removed_field_counts_as_changed(ticket, deployment):
    ov, token = ticket
    final = {k: v for k, v in ITEMS[0].items() if k != "root_cause"}
    edit_of(ov, token, final)
    assert last_event_data(deployment)["edited_fields"] == ["root_cause"]


def test_the_final_payload_hash_uses_the_items_salt(ticket, deployment):
    ov, token = ticket
    final = {**ITEMS[0], "classification": "major"}
    edit_of(ov, token, final)
    [row] = deployment.query("SELECT payload_salt FROM item_registry", planted=True)
    assert last_event_data(deployment)["final_payload_hash"] == payload_hash(
        final, row["payload_salt"]
    )


@pytest.mark.req("REQ-021")
def test_reordering_and_number_spelling_are_not_edits(deployment):
    ov = deployment.open()
    payload = {**ITEMS[0], "root_cause": "x", "extra": {"dose": 10.0, "list": [2.0]}}
    [t] = ov.submit(Item(payload=payload, target_id="rec"))
    ov.shown(t.token, "r-1")
    same = dict(reversed(list({**payload, "extra": {"list": [2], "dose": 10}}.items())))
    with pytest.raises(OversightError, match="unchanged"):
        ov.decided(t.token, "r-1", "edit", final_payload=same)


def test_declared_fields_must_agree(ticket):
    ov, token = ticket
    ov.shown(token, "r-1")
    final = {**ITEMS[0], "classification": "major"}
    for declared in (["root_cause"], [], ["classification", "root_cause"], "classification"):
        with pytest.raises(OversightError, match="does not match what changed: classification"):
            ov.decided(token, "r-1", "edit", declared, final)
    ov.decided(token, "r-1", "edit", ["classification"], final)


@pytest.mark.parametrize(
    ("decision", "arguments", "message"),
    [
        ("edit", {}, "needs final_payload"),
        ("edit", {"final_payload": "text"}, "must be a mapping"),
        ("edit", {"final_payload": {"x": float("nan")}}, "only JSON"),
        ("approve", {"final_payload": ITEMS[0]}, "only an edit"),
        ("reject", {"edited_fields": ["product"]}, "only an edit"),
        ("maybe", {}, "approve, edit or reject"),
    ],
)
def test_bad_decisions(ticket, decision, arguments, message):
    ov, token = ticket
    ov.shown(token, "r-1")
    with pytest.raises(OversightError, match=message):
        ov.decided(token, "r-1", decision, **arguments)


def test_an_edit_that_misses_the_defect_does_not_list_it(make):
    deployment = make()
    ov = deployment.open()
    deployment.load_planted(ov)
    _, planted = submit_until_pair(ov)
    final = {**planted.payload, "description": planted.payload["description"] + " Reviewed."}
    edit_of(ov, planted.token, final, edited_fields=None)
    assert last_event_data(deployment)["edited_fields"] == ["description"]


# ----------------------------------------------------------------- submit checks


@pytest.mark.req("REQ-019")
@pytest.mark.parametrize("target", ["rp-reserved-abc", " RP-RESERVED-1"])
def test_a_reserved_target_id_is_refused(deployment, target):
    with pytest.raises(OversightError, match="reserved"):
        deployment.open().submit(Item(payload=ITEMS[0], target_id=target))
    assert deployment.query("SELECT * FROM items") == []


@pytest.mark.req("REQ-019")
@pytest.mark.parametrize("extra", [{"record": "REC-9"}, {"nested": [{"id": "REC-9"}]}])
def test_the_payload_must_not_hold_the_target_id(deployment, extra):
    with pytest.raises(OversightError, match="contains the target_id"):
        deployment.open().submit(Item(payload={**ITEMS[0], **extra}, target_id="REC-9"))


@pytest.mark.parametrize("payload", [{"x": float("inf")}, {1: "a"}, {"x": object()}])
def test_a_payload_must_be_json(deployment, payload):
    with pytest.raises(OversightError, match="only JSON"):
        deployment.open().submit(Item(payload=payload, target_id="rec"))


def test_the_callers_payload_is_not_shared(deployment):
    ov = deployment.open()
    payload = copy.deepcopy(ITEMS[0])
    [ticket] = ov.submit(Item(payload=payload, target_id="rec"))
    ticket.payload["immediate_actions"].append("added")
    assert payload == ITEMS[0]


@pytest.mark.req("REQ-009")
def test_real_work_that_breaks_the_schema_is_accepted_with_a_warning(deployment, caplog):
    ov = deployment.open()
    bad = {**ITEMS[0], "classification": "SENSITIVE"}
    assert ov.submit(Item(payload=bad, target_id="rec"))
    [record] = caplog.records
    assert record.levelname == "WARNING"
    assert "payload.classification" in record.getMessage()
    assert "SENSITIVE" not in record.getMessage()


@pytest.mark.req("REQ-009")
def test_an_unusable_schema_stops_planting_but_not_real_work(deployment, ov, caplog):
    (deployment.folder / "item.schema.json").write_text('{"oneOf": []}', encoding="utf-8")
    for number in range(10):
        assert len(ov.submit(item(number))) == 1
    assert "cannot be used" in caplog.text
    assert list(deployment.kinds().values()) == ["real"] * 10


@pytest.mark.req("REQ-009")
def test_a_damaged_library_does_not_hold_up_real_work(deployment, ov, caplog):
    with (
        closing(open_planted_db(deployment.planted_db)) as db,
        triggers_removed(db, "planted_errors"),
    ):
        db.execute("UPDATE planted_errors SET payload = 'not json'")
    for number in range(5):
        assert len(ov.submit(item(number))) == 1
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors
    assert "StoreError" in errors[0].getMessage()
    assert "DEMO-" not in caplog.text
    assert deployment.verify().ok


# ----------------------------------------------------------------- storage failures


@pytest.mark.req("REQ-009")
def test_a_lost_registry_stops_submission_and_leaves_no_item(deployment):
    ov = deployment.open()
    with closing(open_planted_db(deployment.planted_db)) as db:
        db.execute("DROP TABLE item_registry")
    with pytest.raises(StoreError, match="item registry"):
        ov.submit(item(0))
    assert deployment.query("SELECT * FROM items") == []
    assert deployment.query("SELECT * FROM events WHERE type = 'submitted'") == []


@pytest.mark.req("REQ-009")
def test_a_locked_log_does_not_use_up_planted_errors(deployment, ov):
    ov._db.execute("PRAGMA busy_timeout = 10")
    with closing(open_oversight_db(deployment.db)) as other:
        other.execute("BEGIN IMMEDIATE")
        for number in range(5):
            with pytest.raises(StoreError, match="cannot lock"):
                ov.submit(item(number))
        other.execute("ROLLBACK")
    assert deployment.kinds() == {}


def test_a_failed_write_leaves_neither_item_nor_event(deployment):
    ov = deployment.open()
    with closing(open_oversight_db(deployment.db)) as db:
        db.execute(
            "CREATE TRIGGER stop BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT, 'disk full'); END"
        )
    with pytest.raises(StoreError, match="disk full"):
        ov.submit(item(0))
    assert deployment.query("SELECT * FROM items") == []
    assert deployment.verify().ok  # the unused registry row is harmless


@pytest.mark.parametrize("break_it", ["no row", "no table", "no hashes"])
def test_an_edit_needs_the_registry_and_the_submission_record(ticket, deployment, break_it):
    ov, token = ticket
    ov.shown(token, "r-1")
    if break_it == "no hashes":
        with closing(open_oversight_db(deployment.db)) as db, triggers_removed(db):
            db.execute("UPDATE events SET data = '{}' WHERE type = 'submitted'")
        expected = "does not match its hash"
    else:
        with closing(open_planted_db(deployment.planted_db)) as db:
            if break_it == "no table":
                db.execute("DROP TABLE item_registry")
                expected = "cannot read the item registry"
            else:
                with triggers_removed(db, "item_registry"):
                    db.execute("DELETE FROM item_registry")
                expected = "no registry row"
    with pytest.raises(StoreError, match=expected):
        ov.decided(token, "r-1", "edit", final_payload={**ITEMS[0], "classification": "major"})
    assert deployment.query("SELECT * FROM events WHERE type = 'decided'") == []


# ----------------------------------------------------------------- setting up


@pytest.mark.req("REQ-045")
def test_opening_never_creates_the_databases(make):
    deployment = make()
    deployment.planted_db.unlink()
    with pytest.raises(StoreError):
        deployment.open()
    assert not deployment.planted_db.exists()


@pytest.mark.req("REQ-045")
def test_a_missing_log_is_not_replaced(make):
    deployment = make()
    deployment.db.unlink()
    with pytest.raises(StoreError):
        deployment.open()
    assert not deployment.db.exists()


@pytest.mark.req("REQ-045")
def test_initialise_is_repeatable_but_never_replaces_one_lost_file(make):
    deployment = make()
    deployment.open().submit(item(0))
    deployment.close()
    initialise(deployment.db, deployment.planted_db)
    assert len(deployment.query("SELECT * FROM items")) == 1
    deployment.planted_db.unlink()
    with pytest.raises(OversightError, match="only one of the two"):
        initialise(deployment.db, deployment.planted_db)
    assert not deployment.planted_db.exists()


def test_the_two_databases_must_differ(tmp_path):
    with pytest.raises(OversightError, match="different files"):
        initialise(tmp_path / "x.db", tmp_path / "x.db")


def test_a_config_path_works_and_the_system_is_checked(deployment):
    path = deployment.folder / "reviewproof.yaml"
    arguments = (path, QUEUE, deployment.db, deployment.planted_db)
    with Oversight(*arguments, system="deviation-classifier") as ov:
        assert ov.submit(item(0))
    with pytest.raises(OversightError, match="belongs to system"):
        Oversight(*arguments, system="another")
    with pytest.raises(OversightError, match="not configured"):
        Oversight(path, "nope", deployment.db, deployment.planted_db)


def test_the_queue_and_context_are_registered_once(deployment):
    deployment.open()
    deployment.open()
    assert len(deployment.query("SELECT * FROM systems")) == 1
    [queue] = deployment.query("SELECT * FROM queues")
    assert (queue["queue_id"], queue["planted_rate"], queue["gxp"]) == (QUEUE, 0.5, 1)
    [event] = deployment.query("SELECT * FROM events")
    assert event["type"] == "context_registered"
    assert json.loads(event["data"])["model_version"] == "1.0"


def test_a_new_model_version_is_a_new_context_and_old_items_keep_theirs(deployment):
    old = deployment.open()
    [ticket] = old.submit(item(0))
    context = deployment.config.systems["deviation-classifier"]
    newer = dataclasses.replace(
        deployment.config,
        systems={context.system: dataclasses.replace(context, model_version="2.0")},
    )
    new = deployment.open(config=newer)
    new.shown(ticket.token, "r-1")
    rows = deployment.query("SELECT type, system_id FROM events ORDER BY event_id")
    systems = [r["system_id"] for r in rows if r["type"] == "context_registered"]
    assert len(set(systems)) == 2
    assert rows[-1] == {"type": "shown", "system_id": systems[0]}
    [queue] = deployment.query("SELECT system_id FROM queues")
    assert queue["system_id"] == systems[1]


def test_a_failed_registration_closes_cleanly(make):
    deployment = make()
    with closing(open_oversight_db(deployment.db)) as db:
        db.execute("DROP TABLE queues")
    with pytest.raises(StoreError):
        deployment.open()
    deployment.db.unlink()  # fails on Windows if a connection was left open


def test_gate_status_is_available(ov):
    status = ov.gate_status()
    assert (status.usable, status.free, status.shortfalls) == (6, 6, 0)


# ----------------------------------------------------------------- many processes


def test_concurrent_hosts(make):
    deployment = make()
    deployment.load_planted(deployment.open())
    errors, payloads = [], []
    start = threading.Barrier(4)

    def host(worker):
        try:
            with Oversight(deployment.config, QUEUE, deployment.db, deployment.planted_db) as ov:
                start.wait()
                for number in range(10):
                    for t in ov.submit(item(number)):
                        payloads.append(t.payload["deviation_id"])
                        ov.shown(t.token, f"r-{worker}")
                        ov.decided(t.token, f"r-{worker}", "approve")
        except BaseException as error:  # reported in the main thread
            errors.append(error)

    threads = [threading.Thread(target=host, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert Counter(deployment.kinds().values())["real"] == 40
    result = deployment.verify()
    assert result.ok, (result.chain_error, result.registry_errors)
    assert len(payloads) == len(deployment.query("SELECT * FROM items"))


def test_the_stored_examples_really_are_in_another_order():
    # Guards the field-order test above: without reordering, these would differ.
    stored = load_file(EXAMPLES[0])["payload"]
    assert sorted(stored) != SCHEMA_ORDER
