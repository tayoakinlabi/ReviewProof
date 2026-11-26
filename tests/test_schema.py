import sqlite3
from contextlib import closing

import pytest

from reviewproof.models import (
    Decision,
    DefectClass,
    EventType,
    FeedbackTiming,
    ItemKind,
    QualificationStatus,
    RiskCategory,
)
from reviewproof.store import (
    StoreError,
    create_oversight_db,
    create_planted_db,
    open_oversight_db,
    open_planted_db,
    schema,
)

TS = "2026-11-26T10:00:00.000000Z"
ZERO = "0" * 64


def h(character):
    return character * 64


def insert(db, table, verb="INSERT", **values):
    columns = ", ".join(values)
    marks = ", ".join("?" * len(values))
    db.execute(f"{verb} INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))


def rows(db, table):
    return [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")]


def tables(db):
    query = "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    return {row[0] for row in db.execute(query)}


def refuses(message=None):
    return pytest.raises(sqlite3.IntegrityError, match=message)


def queue_row(**changes):
    return {
        "queue_id": "q",
        "system_id": "sys-1",
        "task": "task",
        "planted_rate": 0.02,
        "min_review_seconds": 30,
        "vital_base_risk": "A5",
        "gxp": 1,
        "feedback": "batched",
        **changes,
    }


def item_row(**changes):
    return {
        "item_id": "item-1",
        "queue_id": "q",
        "system_id": "sys-1",
        "token_hash": h("a"),
        "payload_hash": h("b"),
        "created_at": TS,
        **changes,
    }


def event_row(event_id=1, prev_hash=ZERO, hash_=None, **changes):
    return {
        "event_id": event_id,
        "format_version": 1,
        "type": "submitted",
        "ts_utc": TS,
        "system_id": "sys-1",
        "data": "{}",
        "prev_hash": prev_hash,
        "hash": hash_ or h(str(event_id)),
        **changes,
    }


def planted_row(**changes):
    return {
        "planted_error_id": "PE-1",
        "queue_id": "q",
        "defect_class": "wrong_value",
        "defect_field": "lot_number",
        "expected_decision": "reject",
        "payload": "{}",
        "reserved_target_id": "rp-reserved-1",
        "author": "author-1",
        "imported_at": TS,
        **changes,
    }


def registry_row(**changes):
    return {
        "item_id": "item-1",
        "kind": "real",
        "planted_error_id": None,
        "target_id": "rec-1",
        "salt": bytes(32),
        "payload_salt": bytes(32),
        **changes,
    }


@pytest.fixture
def oversight(tmp_path):
    with closing(create_oversight_db(tmp_path / "oversight.db")) as db:
        insert(
            db,
            "systems",
            system_id="sys-1",
            fingerprint=h("f"),
            name="s",
            model_id="m",
            model_version="1",
            prompt_version="1",
            tool_schema_hash="t",
            registered_at=TS,
        )
        insert(db, "queues", **queue_row())
        yield db


@pytest.fixture
def planted(tmp_path):
    with closing(create_planted_db(tmp_path / "planted.db")) as db:
        yield db


# ----------------------------------------------------------------- creation and opening


def test_fresh_databases_have_their_tables(oversight, planted):
    assert tables(oversight) - {"schema_version"} == {
        "systems",
        "queues",
        "reviewers",
        "items",
        "events",
        "qualifications",
        "review_context",
        "traces",
    }
    assert tables(planted) - {"schema_version"} == {
        "planted_errors",
        "item_registry",
        "gate_shortfalls",
    }


@pytest.mark.parametrize(
    ("pragma", "expected"),
    [("journal_mode", "wal"), ("foreign_keys", 1), ("recursive_triggers", 1)],
)
def test_connection_settings(oversight, pragma, expected):
    assert oversight.execute(f"PRAGMA {pragma}").fetchone()[0] == expected


def test_oversight_items_cannot_show_which_are_planted(oversight):
    columns = {row["name"] for row in oversight.execute("PRAGMA table_info(items)")}
    assert columns == {
        "item_id",
        "queue_id",
        "system_id",
        "token_hash",
        "payload_hash",
        "created_at",
    }


@pytest.mark.parametrize(
    ("create", "reopen"),
    [(create_oversight_db, open_oversight_db), (create_planted_db, open_planted_db)],
)
def test_create_and_open_are_repeatable(tmp_path, create, reopen):
    path = tmp_path / "with space #1.db"
    create(path).close()
    create(path).close()
    with closing(reopen(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    assert path.is_file()


@pytest.mark.parametrize("reopen", [open_oversight_db, open_planted_db])
def test_opening_a_missing_database_fails_and_creates_nothing(tmp_path, reopen):
    path = tmp_path / "absent.db"
    with pytest.raises(StoreError):
        reopen(path)
    assert not path.exists()


def test_an_empty_file_is_not_opened(tmp_path):
    path = tmp_path / "empty.db"
    path.touch()
    with pytest.raises(StoreError, match="empty"):
        open_planted_db(path)
    assert path.stat().st_size == 0


def test_a_corrupt_file_is_not_opened(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"this is not a database" * 100)
    with pytest.raises(StoreError, match="cannot open"):
        open_planted_db(path)


@pytest.mark.parametrize("reopen", [open_planted_db, create_planted_db])
def test_the_wrong_database_is_refused(tmp_path, reopen):
    path = tmp_path / "x.db"
    create_oversight_db(path).close()
    with pytest.raises(StoreError, match="expected a planted database"):
        reopen(path)


def test_an_unrelated_database_is_left_untouched(tmp_path):
    path = tmp_path / "other.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE mine (x)")
        db.commit()
    before = path.read_bytes()
    with pytest.raises(StoreError, match="other data"):
        create_oversight_db(path)
    assert path.read_bytes() == before


def test_a_newer_schema_is_refused(tmp_path):
    path = tmp_path / "x.db"
    with closing(create_oversight_db(path)) as db:
        db.execute("INSERT INTO schema_version VALUES ('oversight', 99, ?)", (TS,))
    with pytest.raises(StoreError, match="newer than this release"):
        open_oversight_db(path)


def test_a_later_migration_is_applied_to_an_existing_database(tmp_path, monkeypatch):
    path = tmp_path / "x.db"
    create_oversight_db(path).close()
    later = (*schema.OVERSIGHT_MIGRATIONS, ["CREATE TABLE added_later (x)"])
    monkeypatch.setattr(schema, "OVERSIGHT_MIGRATIONS", later)
    with closing(open_oversight_db(path)) as db:
        assert "added_later" in tables(db)
        assert db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 2


def test_a_failed_migration_changes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "x.db"
    create_oversight_db(path).close()
    broken = (*schema.OVERSIGHT_MIGRATIONS, ["CREATE TABLE half (x)", "NOT SQL"])
    monkeypatch.setattr(schema, "OVERSIGHT_MIGRATIONS", broken)
    with pytest.raises(StoreError):
        open_oversight_db(path)
    monkeypatch.undo()
    with closing(open_oversight_db(path)) as db:
        assert "half" not in tables(db)


# ----------------------------------------------------------------- event log


@pytest.fixture
def log(oversight):
    insert(oversight, "events", **event_row(1))
    insert(oversight, "events", **event_row(2, prev_hash=h("1")))
    return oversight


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE events SET decision = 'reject' WHERE event_id = 1",
        "UPDATE OR REPLACE events SET event_id = 1 WHERE event_id = 2",
        "DELETE FROM events WHERE event_id = 2",
        "DELETE FROM events",
    ],
)
def test_events_cannot_be_updated_or_deleted(log, statement):
    before = rows(log, "events")
    with refuses("append-only"):
        log.execute(statement)
    assert rows(log, "events") == before


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize(
    "row",
    [
        # replace an existing event by its ID
        event_row(1, hash_=h("e")),
        # append a valid-looking link whose hash clashes, which would delete the clashing row
        event_row(3, prev_hash=h("2"), hash_=h("1")),
    ],
    ids=["same-id", "same-hash"],
)
def test_events_cannot_be_replaced(log, row, recursive_triggers):
    # Any other connection may leave recursive_triggers off, so protection must not rely on it.
    log.execute(f"PRAGMA recursive_triggers = {recursive_triggers}")
    before = rows(log, "events")
    with refuses():
        insert(log, "events", verb="INSERT OR REPLACE", **row)
    assert rows(log, "events") == before


def test_upsert_cannot_change_an_event(log):
    before = rows(log, "events")
    row = event_row(1)
    columns, marks = ", ".join(row), ", ".join("?" * len(row))
    with refuses():
        log.execute(
            f"INSERT INTO events ({columns}) VALUES ({marks}) "
            "ON CONFLICT (event_id) DO UPDATE SET decision = 'reject'",
            tuple(row.values()),
        )
    assert rows(log, "events") == before


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (event_row(4, prev_hash=h("2")), "next in sequence"),
        (event_row(3, prev_hash=h("1")), "head hash"),  # would fork the chain
        (event_row(3, prev_hash=ZERO), "head hash"),
    ],
)
def test_an_insert_must_extend_the_chain(log, row, message):
    with refuses(message):
        insert(log, "events", **row)


def test_event_id_cannot_be_left_to_sqlite(log):
    row = event_row(3, prev_hash=h("2"))
    del row["event_id"]
    with refuses("next in sequence"):
        insert(log, "events", **row)


@pytest.mark.parametrize(
    "change",
    [
        {"type": "tampered"},
        {"decision": "maybe"},
        {"ts_utc": "2026-11-26 10:00:00"},
        {"hash": "G" * 64},
        {"hash": "abc"},
        {"data": None},
    ],
)
def test_malformed_events_are_refused(oversight, change):
    with refuses():
        insert(oversight, "events", **{**event_row(1), **change})


def test_every_event_type_and_decision_is_accepted(oversight):
    changes = [{"type": e.value} for e in EventType]
    changes += [{"decision": d.value} for d in Decision]
    previous = ZERO
    for number, change in enumerate(changes, start=1):
        digest = f"{number:064x}"
        insert(oversight, "events", **{**event_row(number, previous, digest), **change})
        previous = digest
    assert len(rows(oversight, "events")) == len(changes)


# ----------------------------------------------------------------- other oversight tables


@pytest.mark.parametrize(
    "statement",
    ["UPDATE items SET payload_hash = payload_hash", "DELETE FROM items"],
)
def test_items_are_permanent(oversight, statement):
    insert(oversight, "items", **item_row())
    with refuses("items"):
        oversight.execute(statement)


@pytest.mark.parametrize(
    "row",
    [item_row(token_hash=h("c")), item_row(item_id="item-2")],
    ids=["same-id", "same-token"],
)
def test_items_cannot_be_replaced(oversight, row):
    oversight.execute("PRAGMA recursive_triggers = OFF")
    insert(oversight, "items", **item_row())
    before = rows(oversight, "items")
    with refuses("cannot be replaced"):
        insert(oversight, "items", verb="INSERT OR REPLACE", **row)
    assert rows(oversight, "items") == before


@pytest.mark.parametrize("change", [{"queue_id": "nope"}, {"system_id": "nope"}])
def test_foreign_keys_are_enforced(oversight, change):
    with refuses("FOREIGN KEY"):
        insert(oversight, "items", **item_row(**change))


def test_database_accepts_every_enum_value(oversight):
    for number, status in enumerate(QualificationStatus):
        insert(oversight, "reviewers", reviewer_id=f"r-{number}", qualification_status=status.value)
    for timing in FeedbackTiming:
        insert(oversight, "queues", **queue_row(queue_id=timing.value, feedback=timing.value))
    for category in RiskCategory:
        insert(
            oversight,
            "queues",
            **queue_row(queue_id=category.value, vital_adjusted_category=category.value),
        )


# ----------------------------------------------------------------- planted.db


def approve(db, who, column="approved"):
    db.execute(f"UPDATE planted_errors SET {column}_by = ?, {column}_at = ?", (who, TS))


def retire(db):
    db.execute("UPDATE planted_errors SET retired_at = ?, retired_by = 'qa-1'", (TS,))


def test_retirement_needs_a_name(planted):
    insert(planted, "planted_errors", **planted_row())
    with refuses():
        planted.execute("UPDATE planted_errors SET retired_at = ?", (TS,))


def test_database_accepts_every_defect_class_and_item_kind(planted):
    for number, defect in enumerate(DefectClass):
        insert(
            planted,
            "planted_errors",
            **planted_row(
                planted_error_id=f"PE-{number}",
                reserved_target_id=f"rp-reserved-{number}",
                defect_class=defect.value,
            ),
        )
    insert(planted, "item_registry", **registry_row(kind=ItemKind.REAL.value))
    insert(
        planted,
        "item_registry",
        **registry_row(item_id="item-2", kind=ItemKind.PLANTED.value, planted_error_id="PE-0"),
    )


@pytest.mark.parametrize("expected", ["reject", "edit:lot_number"])
def test_expected_decision_forms(planted, expected):
    insert(planted, "planted_errors", **planted_row(expected_decision=expected))


@pytest.mark.parametrize(
    "change",
    [
        {"expected_decision": "approve"},
        {"expected_decision": "edit:product"},
        {"defect_class": "typo"},
        {"author": " "},
    ],
)
def test_malformed_planted_errors_are_refused(planted, change):
    with refuses():
        insert(planted, "planted_errors", **planted_row(**change))


@pytest.mark.parametrize(
    "change",
    [{"approved_by": "qa-1", "approved_at": TS}, {"retired_at": TS, "retired_by": "qa-1"}],
)
def test_planted_errors_start_unapproved(planted, change):
    with refuses("must be unapproved"):
        insert(planted, "planted_errors", **planted_row(**change))


@pytest.mark.parametrize("who", ["author-1", " Author-1 "])
def test_author_cannot_approve(planted, who):
    insert(planted, "planted_errors", **planted_row())
    with refuses():
        approve(planted, who)


@pytest.mark.parametrize("second", ["qa-1", "QA-1 ", "author-1"])
def test_second_approver_must_be_a_different_person(planted, second):
    insert(planted, "planted_errors", **planted_row())
    approve(planted, "qa-1")
    with refuses():
        approve(planted, second, "second_approved")


def test_second_approval_needs_a_first(planted):
    insert(planted, "planted_errors", **planted_row())
    with refuses():
        approve(planted, "qa-2", "second_approved")


def test_approval_needs_a_time(planted):
    insert(planted, "planted_errors", **planted_row())
    with refuses():
        planted.execute("UPDATE planted_errors SET approved_by = 'qa-1'")


def test_two_approvals_then_retirement(planted):
    insert(planted, "planted_errors", **planted_row())
    approve(planted, "qa-1")
    approve(planted, "qa-2", "second_approved")
    retire(planted)


@pytest.mark.parametrize(
    "column", ["payload", "defect_class", "defect_field", "author", "reserved_target_id"]
)
def test_planted_error_content_is_fixed(planted, column):
    insert(planted, "planted_errors", **planted_row())
    with refuses("content cannot be changed"):
        planted.execute(f"UPDATE planted_errors SET {column} = {column}")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE planted_errors SET approved_by = 'qa-9'",
        "UPDATE planted_errors SET approved_by = NULL, approved_at = NULL",
        "UPDATE planted_errors SET retired_at = NULL",
        "UPDATE planted_errors SET retired_by = 'someone-else'",
    ],
)
def test_approvals_and_retirement_cannot_be_rewritten(planted, statement):
    insert(planted, "planted_errors", **planted_row())
    approve(planted, "qa-1")
    retire(planted)
    with refuses("cannot be changed once set"):
        planted.execute(statement)


@pytest.mark.parametrize(
    "row",
    [planted_row(reserved_target_id="rp-reserved-2"), planted_row(planted_error_id="PE-2")],
    ids=["same-id", "same-target"],
)
def test_planted_errors_cannot_be_replaced_or_deleted(planted, row):
    planted.execute("PRAGMA recursive_triggers = OFF")
    insert(planted, "planted_errors", **planted_row())
    before = rows(planted, "planted_errors")
    with refuses("cannot be replaced"):
        insert(planted, "planted_errors", verb="INSERT OR REPLACE", **row)
    with refuses("retire"):
        planted.execute("DELETE FROM planted_errors")
    assert rows(planted, "planted_errors") == before


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "planted"},  # no planted error named
        {"planted_error_id": "PE-1"},  # a real item naming one
        {"kind": "planted", "planted_error_id": "PE-unknown"},
        {"kind": "unknown"},
        {"target_id": ""},
        {"salt": b"short"},
        {"payload_salt": b"short"},
        {"assigned_reviewer": "r-1"},  # only a planted item is assigned
        {"kind": "planted", "planted_error_id": "PE-1", "assigned_reviewer": " "},
    ],
)
def test_malformed_registry_rows_are_refused(planted, change):
    insert(planted, "planted_errors", **planted_row())
    with refuses():
        insert(planted, "item_registry", **registry_row(**change))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE item_registry SET kind = 'real', planted_error_id = NULL",
        "UPDATE item_registry SET target_id = 'elsewhere'",
        "DELETE FROM item_registry",
    ],
)
def test_registry_rows_cannot_be_relabelled_or_redirected(planted, statement):
    planted.execute("PRAGMA recursive_triggers = OFF")
    insert(planted, "planted_errors", **planted_row())
    insert(planted, "item_registry", **registry_row(kind="planted", planted_error_id="PE-1"))
    before = rows(planted, "item_registry")
    with refuses("item_registry"):
        planted.execute(statement)
    with refuses("cannot be replaced"):
        insert(planted, "item_registry", verb="INSERT OR REPLACE", **registry_row())
    assert rows(planted, "item_registry") == before


@pytest.mark.parametrize("column", ["approved", "second_approved"])
def test_approvals_are_fixed_once_a_planted_error_is_used(planted, column):
    insert(planted, "planted_errors", **planted_row())
    if column == "second_approved":
        approve(planted, "qa-1")
    insert(planted, "item_registry", **registry_row(kind="planted", planted_error_id="PE-1"))
    with refuses("after use"):
        approve(planted, "qa-2", column)
    retire(planted)  # retiring a used planted error is still allowed
