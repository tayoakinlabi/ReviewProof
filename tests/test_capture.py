"""Step traces and review context (step 1.11)."""

import json
import sqlite3
from contextlib import closing

import pytest
from deploy import ITEMS, item, submit_until_pair
from helpers import triggers_removed

from reviewproof import Item, OversightError
from reviewproof.capture import context_digest, review_manifest, trace_digest, trace_records
from reviewproof.store import StoreError, open_oversight_db, open_planted_db

SALT = bytes(range(32))
TS = "2026-11-26T10:00:00.000000Z"
STEPS = [
    {"step_id": "s1", "step_type": "retrieval", "inputs": "SECRET-IN", "outputs": ["SECRET-OUT"]},
    {"step_id": "s2", "step_type": "chat", "ts_utc": TS},
]
CONTEXT = {"sources": ["sop-12"], "steps": ["s1"], "fields": ["classification"]}


def with_trace(trace, number=0):
    return Item(payload=ITEMS[number], target_id=f"rec-{number}", trace=trace)


def rows(deployment, table):
    return deployment.query(f"SELECT * FROM {table}", planted=True)


def everything_in(path):
    with closing(sqlite3.connect(path)) as db:
        return "\n".join(db.iterdump())


# ----------------------------------------------------------------- reducing a trace


def test_only_ids_types_times_and_hashes_are_kept():
    first, second = trace_records(STEPS, SALT)
    assert set(first) == {"step_id", "step_type", "inputs_hash", "outputs_hash", "ts_utc"}
    assert len(first["inputs_hash"]) == len(first["outputs_hash"]) == 64
    assert (second["inputs_hash"], second["outputs_hash"], second["ts_utc"]) == (None, None, TS)
    assert "SECRET" not in json.dumps([first, second])


def test_hashes_depend_on_the_items_salt():
    assert (
        trace_records(STEPS, SALT)[0]["inputs_hash"]
        != trace_records(STEPS, bytes(32))[0]["inputs_hash"]
    )


def test_an_opentelemetry_style_span_is_understood():
    span = {
        "context": {"span_id": "abc"},
        "name": "chat demo-model",
        "attributes": {
            "gen_ai.operation.name": "chat",
            "gen_ai.input.messages": [{"role": "user"}],
            "gen_ai.output.messages": [{"role": "assistant"}],
        },
        "start_time_unix_nano": 1_795_687_200_123_456_789,
        "unrelated": object(),
    }
    tool = {
        "span_id": "def",
        "name": "execute_tool",
        "attributes": {"gen_ai.tool.call.arguments": {"a": 1}, "gen_ai.tool.call.result": 2},
    }
    first, second = trace_records([span, tool], SALT)
    assert (first["step_id"], first["step_type"]) == ("abc", "chat")
    assert first["ts_utc"] == "2026-11-26T10:00:00.123456Z"
    assert (second["step_id"], second["step_type"]) == ("def", "execute_tool")
    for record in (first, second):
        assert record["inputs_hash"] and record["outputs_hash"]


@pytest.mark.parametrize("empty", [None, [], ()])
def test_no_trace(empty):
    assert trace_records(empty, SALT) == []


@pytest.mark.parametrize(
    ("trace", "message"),
    [
        ("steps", "list of steps"),
        ({"step_id": "a"}, "list of steps"),
        (5, "list of steps"),
        (["step"], "must be a mapping"),
        ([{"step_type": "x"}], "needs a text step_id"),
        ([{"step_id": 5, "step_type": "x"}], "needs a text step_id"),
        ([{"step_id": "a"}], "needs a text step_type"),
        ([STEPS[0], STEPS[0]], "used twice"),
        ([{"step_id": "a", "step_type": "x", "attributes": [1]}], "attributes"),
        ([{"step_id": "a", "step_type": "x", "inputs": object()}], "only JSON"),
        ([{"step_id": "a", "step_type": "x", "ts_utc": "yesterday"}], "ts_utc"),
        ([{"step_id": "a", "step_type": "x", "ts_utc": "2026-11-26T10:00:00.1Z"}], "six"),
        ([{"step_id": "a", "step_type": "x", "start_time_unix_nano": "x"}], "not a time"),
        ([{"step_id": "a", "step_type": "x", "start_time_unix_nano": 10**30}], "not a time"),
    ],
)
def test_bad_traces_are_refused(trace, message):
    with pytest.raises(OversightError, match=message):
        trace_records(trace, SALT)


def test_the_trace_digest_ignores_reading_order():
    records = trace_records(STEPS, SALT)
    assert trace_digest(records, SALT) == trace_digest(records[::-1], SALT)
    assert trace_digest(records, SALT) != trace_digest(records[:1], SALT)
    assert trace_digest([], SALT) != trace_digest([], bytes(32))


# ----------------------------------------------------------------- review context


def test_a_manifest_is_completed_with_empty_lists():
    assert review_manifest({"steps": ["a"]}) == {"sources": [], "steps": ["a"], "fields": []}
    assert review_manifest(None) is None
    assert context_digest(None, SALT) != context_digest(review_manifest({}), SALT)


@pytest.mark.parametrize(
    "context",
    ["x", {"screens": []}, {"steps": "s1"}, {"steps": [1]}, {"fields": [""]}],
)
def test_bad_context_is_refused(context):
    with pytest.raises(OversightError, match="context"):
        review_manifest(context)


# ----------------------------------------------------------------- through the API


def test_a_trace_is_stored_in_the_restricted_database_only(deployment):
    ov = deployment.open()
    ov.submit(with_trace(STEPS))
    stored = rows(deployment, "traces")
    assert [r["step_id"] for r in stored] == ["s1", "s2"]
    [item_row] = deployment.query("SELECT item_id FROM items")
    assert {r["item_id"] for r in stored} == {item_row["item_id"]}
    dump = everything_in(deployment.db)
    assert "retrieval" not in dump and "s1" not in dump.replace("items", "")
    for path in (deployment.db, deployment.planted_db):
        assert "SECRET" not in everything_in(path)


def test_a_bad_trace_writes_nothing(deployment):
    ov = deployment.open()
    with pytest.raises(OversightError):
        ov.submit(with_trace([{"step_id": "a"}]))
    assert deployment.query("SELECT * FROM items") == []
    assert rows(deployment, "item_registry") == []


def test_a_failed_trace_write_leaves_no_registry_row(deployment):
    ov = deployment.open()
    with closing(open_planted_db(deployment.planted_db)) as db:
        db.execute("DROP TABLE traces")
    with pytest.raises(StoreError, match="item registry"):
        ov.submit(with_trace(STEPS))
    assert rows(deployment, "item_registry") == []


def test_a_callers_open_transaction_is_not_joined(deployment):
    ov = deployment.open()
    ov._planted.execute("BEGIN")
    with pytest.raises(StoreError, match="already in a transaction"):
        ov.submit(item(0))
    ov._planted.execute("ROLLBACK")


def test_real_items_with_traces_and_planted_items_look_alike_in_the_log(deployment, ov):
    for number in range(20):
        tickets = ov.submit(with_trace(STEPS, number))
        for ticket in tickets:
            ov.shown(ticket.token, "r-1", CONTEXT)
            ov.decided(ticket.token, "r-1", "approve")
    kinds = deployment.kinds()
    assert set(kinds.values()) == {"real", "planted"}
    traced = {r["item_id"] for r in rows(deployment, "traces")}
    assert traced == {i for i, kind in kinds.items() if kind == "real"}  # only real ones

    # Nothing in oversight.db follows that split.
    for event_type in ("submitted", "shown"):
        found = deployment.query("SELECT * FROM events WHERE type = ?", (event_type,))
        datas = [json.loads(r["data"]) for r in found]
        assert len({tuple(sorted(d)) for d in datas}) == 1
        key = "trace_hash" if event_type == "submitted" else "context_hash"
        assert len({d[key] for d in datas}) == len(datas)  # no value repeats
    assert deployment.verify().ok


def test_context_is_stored_per_showing(deployment, ov):
    real, planted = submit_until_pair(ov)
    for ticket in (real, planted):
        ov.shown(ticket.token, "r-1", CONTEXT)
        ov.shown(ticket.token, "r-2")
    stored = rows(deployment, "review_context")
    assert len(stored) == 2
    assert {r["reviewer_id"] for r in stored} == {"r-1"}
    assert json.loads(stored[0]["shown_steps"]) == ["s1"]
    assert "sop-12" not in everything_in(deployment.db)
    assert deployment.verify().ok


def test_bad_context_records_nothing(deployment, ov):
    [ticket, *_] = ov.submit(item(0))
    with pytest.raises(OversightError):
        ov.shown(ticket.token, "r-1", {"steps": "s1"})
    assert deployment.query("SELECT * FROM events WHERE type = 'shown'") == []


def test_a_lost_context_table_stops_the_showing_being_recorded(deployment, ov):
    [ticket, *_] = ov.submit(item(0))
    with closing(open_planted_db(deployment.planted_db)) as db:
        db.execute("DROP TABLE review_context")
    with pytest.raises(StoreError, match="review context"):
        ov.shown(ticket.token, "r-1", CONTEXT)
    assert deployment.query("SELECT * FROM events WHERE type = 'shown'") == []


# ----------------------------------------------------------------- verification


@pytest.fixture
def reviewed(deployment):
    ov = deployment.open()
    [ticket] = ov.submit(with_trace(STEPS))
    ov.shown(ticket.token, "r-1", CONTEXT)
    assert deployment.verify().ok
    return deployment


def tamper(deployment, table, statement):
    with closing(open_planted_db(deployment.planted_db)) as db, triggers_removed(db, table):
        db.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE traces SET step_type = 'other' WHERE step_id = 's1'",
        "UPDATE traces SET inputs_hash = NULL",
        "DELETE FROM traces WHERE step_id = 's2'",
        "INSERT INTO traces SELECT item_id, 's3', 'x', NULL, NULL, NULL FROM item_registry",
    ],
)
def test_a_changed_trace_is_detected(reviewed, statement):
    tamper(reviewed, "traces", statement)
    [message] = reviewed.verify().registry_errors
    assert message.endswith("the trace records do not match the log")


@pytest.mark.parametrize(
    "statement",
    [
        """UPDATE review_context SET shown_steps = '["s1","s2"]'""",
        "UPDATE review_context SET shown_sources = '[]'",
        "UPDATE review_context SET reviewer_id = 'someone-else'",
        "DELETE FROM review_context",
        "UPDATE review_context SET shown_fields = 'not json'",
    ],
)
def test_a_changed_review_context_is_detected(reviewed, statement):
    tamper(reviewed, "review_context", statement)
    [message] = reviewed.verify().registry_errors
    assert "does not match the log" in message


def test_context_rows_left_by_a_crash_are_harmless(reviewed):
    with closing(open_planted_db(reviewed.planted_db)) as db:
        db.execute(
            "INSERT INTO review_context (item_id, reviewer_id, shown_sources, shown_steps, "
            "shown_fields, manifest_hash, ts_utc) SELECT item_id, 'r-1', '[]', '[]', '[]', "
            "salt_hex, ? FROM (SELECT item_id, ? AS salt_hex FROM item_registry)",
            (TS, "a" * 64),
        )
    assert reviewed.verify().ok


def test_a_missing_context_table_is_reported(reviewed):
    with closing(open_planted_db(reviewed.planted_db)) as db:
        db.execute("DROP TABLE review_context")
    [message] = reviewed.verify().registry_errors
    assert message.endswith("the registry cannot be read")


def test_traces_and_context_cannot_be_edited_normally(reviewed):
    with closing(open_planted_db(reviewed.planted_db)) as db:
        for table in ("traces", "review_context"):
            for statement in (f"UPDATE {table} SET item_id = item_id", f"DELETE FROM {table}"):
                with pytest.raises(sqlite3.IntegrityError, match=table):
                    db.execute(statement)
        with pytest.raises(sqlite3.IntegrityError, match="replaced"):
            db.execute("INSERT OR REPLACE INTO traces SELECT * FROM traces")
    with closing(open_oversight_db(reviewed.db)) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {"traces", "review_context"} & names
