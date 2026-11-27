"""Upgrading a version 1 database (as released in 0.1.0) to version 2.

The upgrade rebuilds the event log table, so these tests insist that no stored value or hash
changes, and that a failed upgrade leaves everything as it was.
"""

import sqlite3
from contextlib import closing

import pytest
from deploy import DEMO, QUEUE, Deployment
from helpers import add_item, add_planted_error, fill

from reviewproof import Oversight, initialise
from reviewproof.cli.main import main
from reviewproof.models import EventType, Purpose
from reviewproof.planted import PlantedLibrary
from reviewproof.store import (
    StoreError,
    create_oversight_db,
    create_planted_db,
    open_oversight_db,
    open_planted_db,
    schema,
)
from reviewproof.store.eventlog import EventLog, verify


@pytest.fixture
def version_one(tmp_path, monkeypatch):
    """Databases exactly as version 1 makes them, with some history."""
    paths = tmp_path / "oversight.db", tmp_path / "planted.db"
    with monkeypatch.context() as old:
        old.setattr(schema, "OVERSIGHT_MIGRATIONS", schema.OVERSIGHT_MIGRATIONS[:1])
        old.setattr(schema, "PLANTED_MIGRATIONS", schema.PLANTED_MIGRATIONS[:1])
        with (
            closing(create_oversight_db(paths[0])) as db,
            closing(create_planted_db(paths[1])) as planted,
        ):
            add_planted_error(planted)
            log = EventLog(db)
            add_item(log, planted, "item-1")
            fill(log, 6)
            head = log.head()
    return (*paths, head)


def upgrade_oversight(path):
    return create_oversight_db(path, upgrade=True)


def upgrade_planted(path):
    return create_planted_db(path, upgrade=True)


def version(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]


def events(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT * FROM events ORDER BY event_id").fetchall()


def test_the_log_is_unchanged_by_the_upgrade(version_one):
    db_path, planted_path, head = version_one
    before = events(db_path)
    assert (version(db_path), version(planted_path)) == (1, 1)
    with (
        closing(upgrade_oversight(db_path)) as db,
        closing(upgrade_planted(planted_path)) as planted,
    ):
        result = verify(db, planted, head)
        assert result.ok, (result.chain_error, result.registry_errors)
        assert result.head == head
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert events(db_path) == before
    assert (version(db_path), version(planted_path)) == (2, 2)


def test_the_upgraded_log_is_still_append_only_and_takes_the_new_types(version_one):
    db_path, _, head = version_one
    with closing(upgrade_oversight(db_path)) as db:
        db.execute("PRAGMA recursive_triggers = OFF")
        for statement in (
            "UPDATE events SET system_id = 'x'",
            "DELETE FROM events",
            "INSERT OR REPLACE INTO events SELECT * FROM events WHERE event_id = 1",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement)
        log = EventLog(db)
        for event_type in EventType:
            log.append(event_type, system_id="sys-1")
        assert verify(db, expected=head).ok
        indexes = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "events_by_item" in indexes


def test_old_planted_errors_become_live_ones(version_one):
    _, planted_path, _ = version_one
    with closing(upgrade_planted(planted_path)) as planted:
        row = planted.execute("SELECT purpose FROM planted_errors").fetchone()
        assert row[0] == "live"


@pytest.mark.parametrize(
    ("opener", "which"),
    [
        (open_oversight_db, 0),
        (create_oversight_db, 0),
        (open_planted_db, 1),
        (create_planted_db, 1),
    ],
)
def test_nothing_upgrades_a_database_unasked(version_one, opener, which):
    path = version_one[which]
    before = path.read_bytes()
    with pytest.raises(StoreError, match="schema version 1.*Back up both.*rp init"):
        opener(path)
    assert path.read_bytes() == before
    assert version(path) == 1


@pytest.mark.parametrize("call", ["initialise", "oversight"])
def test_the_python_entry_points_do_not_upgrade_unasked(version_one, tmp_path, call):
    db_path, planted_path, _ = version_one
    before = db_path.read_bytes(), planted_path.read_bytes()
    (tmp_path / "settings").mkdir()
    settings = Deployment(tmp_path / "settings", 0.2).config
    with pytest.raises(StoreError, match="rp init"):
        if call == "initialise":
            initialise(db_path, planted_path)
        else:
            Oversight(settings, QUEUE, db_path, planted_path)
    assert (db_path.read_bytes(), planted_path.read_bytes()) == before


def test_initialise_upgrades_when_asked(version_one):
    db_path, planted_path, _ = version_one
    initialise(db_path, planted_path, upgrade=True)
    assert (version(db_path), version(planted_path)) == (2, 2)
    open_oversight_db(db_path).close()
    open_planted_db(planted_path).close()


def test_rp_init_upgrades_and_says_so(version_one, capsys):
    db_path, planted_path, head = version_one
    arguments = ["init", "--db", str(db_path), "--planted", str(planted_path)]
    assert main(arguments) == 0
    out = capsys.readouterr().out
    assert f"Upgraded {db_path}. Earlier releases can no longer open it." in out
    assert f"Upgraded {planted_path}." in out
    assert (version(db_path), version(planted_path)) == (2, 2)
    with closing(schema.open_readonly(db_path, "oversight")) as db:
        assert verify(db, expected=head).ok
    assert main(arguments) == 0
    assert "Upgraded" not in capsys.readouterr().out


def test_other_commands_refuse_an_old_database(version_one, capsys):
    _, planted_path, _ = version_one
    before = planted_path.read_bytes()
    files = ["--config", str(DEMO / "reviewproof.yaml"), "--planted", str(planted_path)]
    arguments = ["plant", "retire", *files, "PE-1"]
    assert main([*arguments, "--by", "qa", "--reason", "test"]) == 2
    assert "rp init" in capsys.readouterr().err
    assert planted_path.read_bytes() == before


def test_reading_without_upgrading_works_and_changes_nothing(version_one):
    db_path, planted_path, head = version_one
    before = db_path.read_bytes(), planted_path.read_bytes()
    with (
        closing(schema.open_readonly(db_path, "oversight")) as db,
        closing(schema.open_readonly(planted_path, "planted")) as planted,
    ):
        assert verify(db, planted, head).ok
        config = type("C", (), {"queues": {}})()
        [old] = PlantedLibrary(planted, config).all()
        assert old.purpose is Purpose.LIVE
    assert (db_path.read_bytes(), planted_path.read_bytes()) == before
    assert version(db_path) == 1


def test_a_copy_that_is_not_exact_undoes_the_upgrade(version_one, monkeypatch):
    db_path, _, _ = version_one
    before = events(db_path)
    second = list(schema.OVERSIGHT_MIGRATIONS[1])
    check = second.index(schema._check_log_copied)
    damage = "UPDATE events_v2 SET system_id = 'altered' WHERE event_id = 3"
    second.insert(check, damage)
    monkeypatch.setattr(schema, "OVERSIGHT_MIGRATIONS", (schema.OVERSIGHT_MIGRATIONS[0], second))
    with pytest.raises(StoreError, match="not copied exactly"):
        upgrade_oversight(db_path)
    assert version(db_path) == 1
    assert events(db_path) == before
    with closing(sqlite3.connect(db_path)) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
    assert "events_v2" not in names and "events_no_update" in names


def test_a_new_database_has_the_same_log_table_as_an_upgraded_one(version_one, tmp_path):
    db_path, _, _ = version_one
    upgrade_oversight(db_path).close()
    create_oversight_db(tmp_path / "new.db").close()

    def definitions(path):
        with closing(sqlite3.connect(path)) as db:
            return sorted(
                row[0]
                for row in db.execute(
                    "SELECT sql FROM sqlite_master WHERE tbl_name = 'events' AND sql IS NOT NULL"
                )
            )

    assert definitions(db_path) == definitions(tmp_path / "new.db")
