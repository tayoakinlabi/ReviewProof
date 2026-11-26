import json
import sqlite3
from contextlib import closing

import pytest
from helpers import add_item, add_planted_error, fill, triggers_removed

from reviewproof.cli.main import main
from reviewproof.models import EventType, ItemKind
from reviewproof.store import StoreError, schema
from reviewproof.store.eventlog import EventLog, Head, compute_hash, verify


@pytest.fixture
def log(event_log):
    fill(event_log, 5)
    return event_log


def tamper(db, statement, arguments=()):
    with triggers_removed(db):
        db.execute(statement, arguments)


def rehash(db, event_id):
    """What a careful forger does after an edit: recompute that event's own hash."""
    row = dict(db.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone())
    row["data"] = json.loads(row["data"])
    tamper(db, "UPDATE events SET hash = ? WHERE event_id = ?", (compute_hash(row), event_id))


# ----------------------------------------------------------------- the chain


def test_a_clean_log_verifies(log, oversight_db):
    result = verify(oversight_db)
    assert result.ok
    assert result.broken_at is None
    assert result.head == log.head()
    assert not result.registry_checked


@pytest.mark.parametrize(
    "change",
    [
        "decision = 'reject'",
        "reviewer_id = 'r-2'",
        "reviewer_id = NULL",
        "item_id = 'item-9'",
        "system_id = 'sys-2'",
        "queue_id = 'other'",
        "type = 'blocked'",
        "ts_utc = '2020-01-01T00:00:00.000000Z'",
        """data = '{"dwell_ms":999999}'""",
        """data = '{"dwell_ms": 3000}'""",  # same meaning, different text
        """data = '{"dwell_ms":1,"dwell_ms":3000}'""",  # duplicate key hides a value
        """data = '{"dwell_ms":3000.0}'""",
        "data = '[1]'",
        "data = 'not json'",
        "data = '{\"x\":NaN}'",
        "data = x'00ff'",
        "system_id = x'00ff'",
        "format_version = 2",
        "format_version = 1.5",
        f"hash = '{'e' * 64}'",
    ],
)
def test_any_edit_is_reported_at_that_event(log, oversight_db, change):
    tamper(oversight_db, f"UPDATE events SET {change} WHERE event_id = 3")
    result = verify(oversight_db)
    assert not result.ok
    assert result.broken_at == 3
    assert result.head.event_count == 2


def test_text_that_is_not_utf8_is_reported_not_raised(log, oversight_db):
    tamper(oversight_db, "UPDATE events SET system_id = CAST(x'ff' AS TEXT) WHERE event_id = 3")
    result = verify(oversight_db)
    assert result.broken_at == 3
    assert result.head.event_count == 2
    assert "wrong type" in result.chain_error
    assert oversight_db.text_factory is str  # the caller's connection is left as it was


def test_a_log_that_cannot_be_read_is_reported_not_raised(log, oversight_db):
    oversight_db.execute("DROP TABLE events")
    result = verify(oversight_db)
    assert not result.ok
    assert "cannot be read" in result.chain_error


def test_an_edit_with_a_recomputed_hash_breaks_the_next_link(log, oversight_db):
    tamper(oversight_db, "UPDATE events SET decision = 'reject' WHERE event_id = 3")
    rehash(oversight_db, 3)
    result = verify(oversight_db)
    assert result.broken_at == 4
    assert "does not follow" in result.chain_error


@pytest.mark.parametrize("event_id", [1, 3])
def test_a_removed_event_is_reported(log, oversight_db, event_id):
    tamper(oversight_db, "DELETE FROM events WHERE event_id = ?", (event_id,))
    result = verify(oversight_db)
    assert result.broken_at == event_id
    assert "missing" in result.chain_error


def test_swapped_events_are_reported(log, oversight_db):
    for old, new in [(2, 100), (3, 2), (100, 3)]:
        tamper(oversight_db, "UPDATE events SET event_id = ? WHERE event_id = ?", (new, old))
    assert verify(oversight_db).broken_at == 2


# ----------------------------------------------------------------- the exported head


def test_removing_the_latest_events_needs_the_exported_head(log, oversight_db):
    exported = log.head()
    tamper(oversight_db, "DELETE FROM events WHERE event_id > 3")
    assert verify(oversight_db).ok  # the chain alone cannot see this
    result = verify(oversight_db, expected=exported)
    assert not result.ok
    assert "missing or changed" in result.anchor_error


def test_a_rewritten_log_needs_the_exported_head(log, oversight_db):
    exported = log.head()
    tamper(oversight_db, "DELETE FROM events")
    fill(EventLog(oversight_db), 7)  # a consistent, longer, different history
    assert verify(oversight_db).ok
    result = verify(oversight_db, expected=exported)
    assert "rebuilt or rewritten" in result.anchor_error


def test_a_log_that_has_grown_still_matches_an_earlier_head(log, oversight_db):
    exported = log.head()
    fill(log, 3)
    assert verify(oversight_db, expected=exported).ok


def test_an_empty_export_matches_any_log(log, oversight_db):
    assert verify(oversight_db, expected=Head(0, schema.GENESIS_HASH)).ok
    assert not verify(oversight_db, expected=Head(0, "e" * 64)).ok


# ----------------------------------------------------------------- the registry


@pytest.fixture
def items(event_log, registry_db):
    add_planted_error(registry_db)
    add_item(event_log, registry_db, "item-a")
    add_item(event_log, registry_db, "item-b", ItemKind.PLANTED, "PE-1")
    add_item(event_log, registry_db, "item-c")


def test_an_untouched_registry_verifies(items, oversight_db, registry_db):
    result = verify(oversight_db, registry_db)
    assert result.ok, result.registry_errors
    assert result.registry_checked


@pytest.mark.parametrize(
    "change",
    [
        # relabel a planted error as real, so that a miss disappears
        "kind = 'real', planted_error_id = NULL",
        "target_id = 'another-record'",
        "salt = zeroblob(32)",
    ],
)
def test_a_changed_registry_row_is_detected(items, oversight_db, registry_db, change):
    with triggers_removed(registry_db, "item_registry"):
        registry_db.execute(f"UPDATE item_registry SET {change} WHERE item_id = 'item-b'")
    result = verify(oversight_db, registry_db)
    assert not result.ok
    assert result.registry_errors == (
        "event 2, item item-b: the registry row does not match the log",
    )
    assert result.chain_error is None


def test_a_real_item_relabelled_as_planted_is_detected(items, oversight_db, registry_db):
    with triggers_removed(registry_db, "item_registry"):
        registry_db.execute(
            "UPDATE item_registry SET kind = 'planted', planted_error_id = 'PE-1' "
            "WHERE item_id = 'item-a'"
        )
    assert len(verify(oversight_db, registry_db).registry_errors) == 1


def test_a_missing_registry_row_is_detected(items, oversight_db, registry_db):
    with triggers_removed(registry_db, "item_registry"):
        registry_db.execute("DELETE FROM item_registry WHERE item_id = 'item-a'")
    assert "no registry row" in verify(oversight_db, registry_db).registry_errors[0]


def test_an_unused_registry_row_is_harmless(items, oversight_db, registry_db):
    registry_db.execute(
        "INSERT INTO item_registry VALUES ('orphan', 'real', NULL, 't', zeroblob(32), zeroblob(32))"
    )
    assert verify(oversight_db, registry_db).ok


def test_a_submission_without_a_commitment_is_detected(event_log, oversight_db, registry_db):
    registry_db.execute(
        "INSERT INTO item_registry VALUES ('x', 'real', NULL, 't', zeroblob(32), zeroblob(32))"
    )
    event_log.append(EventType.SUBMITTED, system_id="sys-1", item_id="x")
    assert not verify(oversight_db, registry_db).ok


def test_a_second_submission_is_detected(items, event_log, oversight_db):
    event_log.append(EventType.SUBMITTED, system_id="sys-1", item_id="item-a")
    assert "submitted twice" in verify(oversight_db).registry_errors[0]


def test_an_unreadable_registry_is_reported(items, oversight_db, registry_db):
    registry_db.execute("DROP TABLE item_registry")
    errors = verify(oversight_db, registry_db).registry_errors
    assert len(errors) == 3
    assert "cannot be read" in errors[0]


# ----------------------------------------------------------------- rp verify


def rp(*arguments):
    return main(["verify", *map(str, arguments)])


def test_cli_clean_log(log, oversight_path, capsys):
    assert rp("--db", oversight_path) == 0
    out = capsys.readouterr().out
    assert "Chain intact: 5 events." in out
    assert log.head().head_hash in out
    assert "not checked" in out
    assert "without --expect-head" in out


def test_cli_default_database_is_in_the_current_folder(log, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert rp() == 0


def test_cli_names_the_first_broken_link(log, oversight_db, oversight_path, capsys):
    tamper(oversight_db, "UPDATE events SET decision = 'reject' WHERE event_id IN (2, 4)")
    assert rp("--db", oversight_path) == 1
    out = capsys.readouterr().out
    assert "FAILED: the chain is broken at event 2." in out
    assert "Events before it that verify: 1" in out


def test_cli_export_then_expect(log, oversight_db, oversight_path, tmp_path, capsys):
    head_file = tmp_path / "head.json"
    assert rp("--db", oversight_path, "--export-head", head_file) == 0
    content = json.loads(head_file.read_text(encoding="utf-8"))
    assert content["event_count"] == 5
    assert content["head_hash"] == log.head().head_hash
    assert rp("--db", oversight_path, "--expect-head", head_file) == 0
    assert "found at event 5" in capsys.readouterr().out

    tamper(oversight_db, "DELETE FROM events WHERE event_id = 5")
    assert rp("--db", oversight_path, "--expect-head", head_file) == 1
    assert "FAILED: the exported head check." in capsys.readouterr().out


def test_cli_never_overwrites_an_export(log, oversight_path, tmp_path, capsys):
    head_file = tmp_path / "head.json"
    head_file.write_text("earlier evidence", encoding="utf-8")
    assert rp("--db", oversight_path, "--export-head", head_file) == 2
    assert head_file.read_text(encoding="utf-8") == "earlier evidence"
    assert "cannot export" in capsys.readouterr().err


def test_cli_does_not_export_a_failed_log(log, oversight_db, oversight_path, tmp_path):
    tamper(oversight_db, "UPDATE events SET decision = 'reject' WHERE event_id = 2")
    head_file = tmp_path / "head.json"
    assert rp("--db", oversight_path, "--export-head", head_file) == 1
    assert not head_file.exists()


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        "{}",
        '{"format_version": 9, "event_count": 1, "head_hash": "x"}',
        '{"format_version": 1, "event_count": -1, "head_hash": "x"}',
        '{"format_version": 1, "event_count": 1, "head_hash": "short"}',
        '{"format_version": 1, "event_count": 1, "head_hash": null}',
    ],
)
def test_cli_bad_head_file(log, oversight_path, tmp_path, content, capsys):
    head_file = tmp_path / "head.json"
    head_file.write_text(content, encoding="utf-8")
    assert rp("--db", oversight_path, "--expect-head", head_file) == 2
    assert "cannot read the exported head" in capsys.readouterr().err


def test_cli_registry_problems_do_not_say_which_items_are_planted(
    items, oversight_path, planted_path, registry_db, capsys
):
    assert rp("--db", oversight_path, "--planted", planted_path) == 0
    assert "matches the log" in capsys.readouterr().out
    with triggers_removed(registry_db, "item_registry"):
        registry_db.execute("UPDATE item_registry SET target_id = 'x'")
    assert rp("--db", oversight_path, "--planted", planted_path) == 1
    out = capsys.readouterr().out
    assert "3 problem(s)" in out
    assert not any(word in out for word in ("planted", "real", "PE-1"))


def test_cli_shortens_a_long_list_of_problems(event_log, registry_db, oversight_path, capsys):
    for number in range(25):
        event_log.append(EventType.SUBMITTED, system_id="sys-1", item_id=f"i-{number}")
    planted_path = oversight_path.with_name("planted.db")
    assert rp("--db", oversight_path, "--planted", planted_path) == 1
    assert "... and 5 more" in capsys.readouterr().out


@pytest.mark.parametrize("option", ["--db", "--planted"])
def test_cli_missing_database(log, oversight_path, tmp_path, option, capsys):
    arguments = ["--db", oversight_path, option, tmp_path / "absent.db"]
    assert rp(*arguments) == 2
    assert "error:" in capsys.readouterr().err
    assert not (tmp_path / "absent.db").exists()


# ----------------------------------------------------------------- verification changes nothing


def snapshot(folder):
    return {path.name: path.read_bytes() for path in folder.glob("*.db")}


def test_verifying_does_not_change_the_databases(
    items, oversight_db, registry_db, oversight_path, planted_path
):
    oversight_db.close()
    registry_db.close()
    before = snapshot(oversight_path.parent)
    assert rp("--db", oversight_path, "--planted", planted_path) == 0
    assert snapshot(oversight_path.parent) == before


def test_a_readonly_connection_cannot_write(log, oversight_path):
    with (
        closing(schema.open_readonly(oversight_path, "oversight")) as db,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        db.execute("CREATE TABLE x (y)")


def test_readonly_refuses_a_newer_schema(log, oversight_db, oversight_path):
    oversight_db.execute("INSERT INTO schema_version VALUES ('oversight', 99, 'now')")
    with pytest.raises(StoreError, match="newer"):
        schema.open_readonly(oversight_path, "oversight")


def test_readonly_refuses_the_wrong_or_a_corrupt_database(oversight_db, oversight_path, tmp_path):
    with pytest.raises(StoreError, match="expected a planted"):
        schema.open_readonly(oversight_path, "planted")
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a database" * 100)
    with pytest.raises(StoreError, match="cannot open"):
        schema.open_readonly(corrupt, "oversight")
