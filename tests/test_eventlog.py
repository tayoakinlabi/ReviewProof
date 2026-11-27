import json
import subprocess
import sys
import threading
from contextlib import closing

import pytest
from helpers import fill

from reviewproof.canonical import canonical_json, sha256_hex
from reviewproof.models import Decision, EventType
from reviewproof.store import StoreError, open_oversight_db
from reviewproof.store.eventlog import EventLog, verify
from reviewproof.store.schema import GENESIS_HASH

TS = "2026-11-26T10:00:00.000000Z"


def test_empty_log(event_log, oversight_db):
    assert event_log.head().event_count == 0
    assert event_log.head().head_hash == GENESIS_HASH
    assert verify(oversight_db).ok


@pytest.mark.req("REQ-025")
def test_the_hash_follows_the_documented_format(oversight_db):
    """Rebuild the hash from ADR-002 without using the module's own function."""
    log = EventLog(oversight_db, clock=lambda: TS)
    event = log.append(
        EventType.DECIDED,
        system_id="sys-1",
        item_id="item-1",
        decision=Decision.REJECT,
        data={"note": "µg"},
    )
    record = {
        "event_id": 1,
        "format_version": 1,
        "type": "decided",
        "ts_utc": TS,
        "system_id": "sys-1",
        "queue_id": None,
        "item_id": "item-1",
        "reviewer_id": None,
        "decision": "reject",
        "data": {"note": "µg"},
        "prev_hash": GENESIS_HASH,
    }
    assert event.hash == sha256_hex(GENESIS_HASH.encode() + canonical_json(record))
    assert event.type is EventType.DECIDED
    assert event.decision is Decision.REJECT


def test_appends_are_chained(event_log, oversight_db):
    first = event_log.append(EventType.SHOWN, system_id="sys-1")
    second = event_log.append(EventType.OPENED, system_id="sys-1")
    assert (first.event_id, second.event_id) == (1, 2)
    assert second.prev_hash == first.hash
    assert event_log.head().head_hash == second.hash
    result = verify(oversight_db)
    assert result.ok
    assert result.head == event_log.head()


def test_changing_the_data_afterwards_does_not_change_the_event(event_log, oversight_db):
    data = {"fields": ["a"]}
    event = event_log.append(EventType.EDITED, system_id="sys-1", data=data)
    data["fields"].append("b")
    assert event.data == {"fields": ["a"]}
    assert verify(oversight_db).ok


@pytest.mark.parametrize(
    "data",
    [{"x": 1.5}, {"x": [{"y": 2.0}]}, {"x": float("nan")}, {1: "a"}, {"x": (1.5,)}, {"x": b"b"}],
)
def test_bad_data_is_refused_and_nothing_is_written(event_log, data):
    with pytest.raises((TypeError, ValueError)):
        event_log.append(EventType.SHOWN, system_id="sys-1", data=data)
    assert event_log.head().event_count == 0


@pytest.mark.parametrize(
    "arguments",
    [
        {"system_id": ""},
        {"system_id": 5},
        {"system_id": "s", "item_id": 5},
        {"system_id": "s", "reviewer_id": b"r"},
        {"system_id": "s", "decision": "maybe"},
        {"system_id": "\ud800"},
    ],
)
def test_bad_arguments_are_refused(event_log, arguments):
    with pytest.raises((TypeError, ValueError)):
        event_log.append(EventType.SHOWN, **arguments)
    assert event_log.head().event_count == 0


def test_unknown_event_type_is_refused(event_log):
    with pytest.raises(ValueError):
        event_log.append("invented", system_id="sys-1")


def test_a_bad_clock_cannot_write_an_event(oversight_db):
    log = EventLog(oversight_db, clock=lambda: "yesterday")
    with pytest.raises(StoreError):
        log.append(EventType.SHOWN, system_id="sys-1")
    assert not oversight_db.in_transaction
    assert log.head().event_count == 0


@pytest.mark.req("REQ-029")
def test_a_transaction_commits_or_rolls_back_as_a_whole(event_log, oversight_db):
    with event_log.transaction():
        fill(event_log, 2)
    assert event_log.head().event_count == 2

    with pytest.raises(RuntimeError), event_log.transaction():
        fill(event_log, 3)
        raise RuntimeError("stop")
    assert event_log.head().event_count == 2
    assert not oversight_db.in_transaction
    assert verify(oversight_db).ok


def test_a_foreign_transaction_is_refused(event_log, oversight_db):
    oversight_db.execute("BEGIN")
    with pytest.raises(StoreError, match="transaction"):
        event_log.append(EventType.SHOWN, system_id="sys-1")
    oversight_db.execute("ROLLBACK")


@pytest.mark.req("REQ-029")
def test_a_locked_log_raises_store_error(event_log, oversight_path):
    with closing(open_oversight_db(oversight_path)) as other:
        other.execute("PRAGMA busy_timeout = 10")
        with event_log.transaction(), pytest.raises(StoreError, match="cannot lock"):
            EventLog(other).append(EventType.SHOWN, system_id="sys-1")


def test_a_closed_database_raises_store_error(oversight_path):
    from reviewproof.store import create_oversight_db

    db = create_oversight_db(oversight_path)
    log = EventLog(db)
    db.close()
    with pytest.raises(StoreError):
        log.head()


#: These two tests are about the chain, not speed. SQLite does not queue waiting writers, so
#: on a slow disk one can wait past the usual limit and be refused, which is correct: see
#: test_a_locked_log_raises_store_error.
PATIENT = "PRAGMA busy_timeout = 60000"


@pytest.mark.req("REQ-029")
def test_concurrent_threads_cannot_fork_the_chain(oversight_db, oversight_path):
    writers, each = 6, 40
    errors = []
    start = threading.Barrier(writers)

    def write():
        try:
            with closing(open_oversight_db(oversight_path)) as db:
                db.execute(PATIENT)
                log = EventLog(db)
                start.wait()
                for _ in range(each):
                    log.append(EventType.SHOWN, system_id="sys-1")
        except BaseException as error:  # reported in the main thread
            errors.append(error)

    threads = [threading.Thread(target=write) for _ in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    result = verify(oversight_db)
    assert result.ok, result.chain_error
    assert result.head.event_count == writers * each


WRITER = """
import sys
from reviewproof.models import EventType
from reviewproof.store import open_oversight_db
from reviewproof.store.eventlog import EventLog
db = open_oversight_db(sys.argv[1])
db.execute(sys.argv[3])
log = EventLog(db)
for _ in range(int(sys.argv[2])):
    log.append(EventType.SHOWN, system_id="sys-1")
"""


@pytest.mark.req("REQ-029")
def test_concurrent_processes_cannot_fork_the_chain(oversight_db, oversight_path):
    command = [sys.executable, "-c", WRITER, str(oversight_path), "30", PATIENT]
    processes = [subprocess.Popen(command, stderr=subprocess.PIPE) for _ in range(4)]
    for process in processes:
        _, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, stderr.decode()
    result = verify(oversight_db)
    assert result.ok, result.chain_error
    assert result.head.event_count == 120


def test_stored_data_is_canonical_text(event_log, oversight_db):
    event_log.append(EventType.SHOWN, system_id="sys-1", data={"b": 1, "a": [True, None]})
    stored = oversight_db.execute("SELECT data FROM events").fetchone()[0]
    assert stored == '{"a":[true,null],"b":1}'
    assert json.loads(stored) == {"a": [True, None], "b": 1}
