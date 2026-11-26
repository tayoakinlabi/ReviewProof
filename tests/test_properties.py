"""Property tests for the hash chain (step 1.13): many random logs and random tampering."""

import tempfile
from contextlib import closing
from pathlib import Path

from helpers import triggers_removed
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from reviewproof.models import Decision, EventType
from reviewproof.store import create_oversight_db
from reviewproof.store.eventlog import EventLog, item_events, verify

SETTINGS = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])

# Any text that can be encoded (no surrogates), including NUL.
text = st.text(st.characters(blacklist_categories=["Cs"]), max_size=12)
names = text.filter(bool)
json_values = st.recursive(
    st.none() | st.booleans() | st.integers(-(2**62), 2**62) | text,
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(text, inner, max_size=3),
    max_leaves=8,
)
events = st.fixed_dictionaries(
    {
        "event_type": st.sampled_from(list(EventType)),
        "system_id": names,
        "queue_id": st.none() | text,
        "item_id": st.none() | st.sampled_from(["a", "b"]),
        "reviewer_id": st.none() | text,
        "decision": st.none() | st.sampled_from(list(Decision)),
        "data": st.dictionaries(text, json_values, max_size=3),
    }
)


def build(folder, entries):
    db = create_oversight_db(Path(folder) / "o.db")
    log = EventLog(db)
    for entry in entries:
        log.append(entry["event_type"], **{k: v for k, v in entry.items() if k != "event_type"})
    return db


@SETTINGS
@given(st.lists(events, max_size=8))
def test_any_appended_log_verifies(entries):
    with tempfile.TemporaryDirectory() as folder, closing(build(folder, entries)) as db:
        result = verify(db)
        # SUBMITTED twice for one item is reported separately; the chain is still intact.
        assert result.chain_error is None, result.chain_error
        assert result.head.event_count == len(entries)
        for item_id in ("a", "b"):
            assert len(item_events(db, item_id)) == sum(e["item_id"] == item_id for e in entries)


COLUMNS = (
    "type",
    "ts_utc",
    "system_id",
    "queue_id",
    "item_id",
    "reviewer_id",
    "decision",
    "data",
    "prev_hash",
    "hash",
)
replacement = {
    "type": st.sampled_from([e.value for e in EventType]),
    "ts_utc": st.just("2001-01-01T00:00:00.000000Z"),
    "decision": st.none() | st.sampled_from([d.value for d in Decision]),
    "data": st.sampled_from(['{"x":1}', "{}", '{ "x": 1}']),
    "prev_hash": st.sampled_from(["0" * 64, "a" * 64]),
    "hash": st.just("b" * 64),
}


@SETTINGS
@given(st.lists(events, min_size=1, max_size=6), st.data())
def test_changing_any_stored_value_is_detected_at_that_event(entries, data):
    with tempfile.TemporaryDirectory() as folder, closing(build(folder, entries)) as db:
        target = data.draw(st.integers(1, len(entries)))
        column = data.draw(st.sampled_from(COLUMNS))
        new = data.draw(replacement.get(column, st.none() | names))
        old = db.execute(f"SELECT {column} FROM events WHERE event_id = ?", (target,)).fetchone()
        assume(new != old[0])
        required = column in ("type", "ts_utc", "system_id", "data", "prev_hash", "hash")
        assume(not (required and new is None))
        with triggers_removed(db):
            db.execute(f"UPDATE events SET {column} = ? WHERE event_id = ?", (new, target))
        result = verify(db)
        assert result.broken_at == target, (column, new, result.chain_error)


@SETTINGS
@given(st.lists(events, min_size=2, max_size=6), st.data())
def test_removing_any_event_but_the_last_is_detected(entries, data):
    with tempfile.TemporaryDirectory() as folder, closing(build(folder, entries)) as db:
        target = data.draw(st.integers(1, len(entries) - 1))
        with triggers_removed(db):
            db.execute("DELETE FROM events WHERE event_id = ?", (target,))
        assert verify(db).broken_at == target
