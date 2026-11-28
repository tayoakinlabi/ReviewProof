"""Upgrading databases of earlier schema versions: version 1, as released in 0.1.0, and
later ones from the repository's source. ``oversight.db`` is now at version 4 and
``planted.db`` at version 3.

Versions 2 and 4 rebuild the event log table, version 3 replaces the column that holds a
queue's risk category, and version 3 of ``planted.db`` adds the void records. So these tests
insist that no stored value or hash changes, and that a failed upgrade leaves everything as it
was.
"""

import dataclasses
import sqlite3
from contextlib import closing

import pytest
from deploy import DEMO, QUEUE, Deployment, item
from helpers import add_item, add_planted_error, fill

from reviewproof import Oversight, initialise
from reviewproof.cli.main import main
from reviewproof.models import EventType, Purpose, RiskCategory
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


#: The latest versions of oversight.db and planted.db.
LATEST = (4, 3)


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


@pytest.mark.req("REQ-046")
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
    assert (version(db_path), version(planted_path)) == LATEST


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


@pytest.mark.req("REQ-046")
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
    assert (version(db_path), version(planted_path)) == LATEST
    open_oversight_db(db_path).close()
    open_planted_db(planted_path).close()


def test_rp_init_upgrades_and_says_so(version_one, capsys):
    db_path, planted_path, head = version_one
    arguments = ["init", "--db", str(db_path), "--planted", str(planted_path)]
    assert main(arguments) == 0
    out = capsys.readouterr().out
    assert f"Upgraded {db_path}. Earlier releases can no longer open it." in out
    assert f"Upgraded {planted_path}." in out
    assert (version(db_path), version(planted_path)) == LATEST
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


@pytest.mark.req("REQ-046")
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


# ----------------------------------------------------------------- version 3 (ADR-007)


def with_category(config, category):
    queue = dataclasses.replace(config.queues[QUEUE], vital_adjusted_category=category)
    return dataclasses.replace(config, queues={QUEUE: queue})


@pytest.fixture
def version_two(tmp_path, monkeypatch):
    """Make databases exactly as version 2 had them, with reviews, so that items refer to the
    queue. Returns a function that takes the queue's stored risk category.
    """
    made = []

    def make(category=RiskCategory.HIGH):
        (tmp_path / "v2").mkdir()
        with monkeypatch.context() as old:
            old.setattr(schema, "OVERSIGHT_MIGRATIONS", schema.OVERSIGHT_MIGRATIONS[:2])
            old.setattr(schema, "PLANTED_MIGRATIONS", schema.PLANTED_MIGRATIONS[:2])
            deployment = Deployment(tmp_path / "v2", 0.2)
            made.append(deployment)
            deployment.config = with_category(deployment.config, category)
            ov = deployment.open()
            for number in range(6):
                [ticket, *_] = ov.submit(item(number))
                ov.shown(ticket.token, "r-1")
                ov.decided(ticket.token, "r-1", "approve")
            ov.close()
        with closing(sqlite3.connect(deployment.db)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return deployment

    yield make
    for deployment in made:  # whatever the test opened afterwards
        deployment.close()


def read(path, statement):
    with closing(sqlite3.connect(path)) as db:
        return db.execute(statement).fetchall()


def definition(path, table):
    [(sql,)] = read(path, f"SELECT sql FROM sqlite_master WHERE name = '{table}'")
    return sql


@pytest.mark.req("REQ-046")
@pytest.mark.parametrize(
    "category", [None, RiskCategory.LOW, RiskCategory.MEDIUM, RiskCategory.HIGH]
)
def test_version_3_keeps_every_row_and_stored_rating(version_two, category):
    deployment = version_two(category)
    with closing(schema.open_readonly(deployment.db, "oversight")) as db:
        head = verify(db).head
    kept = ["SELECT * FROM items ORDER BY item_id", "SELECT * FROM events ORDER BY event_id"]
    rest = "SELECT queue_id, system_id, task, planted_rate, vital_base_risk, site FROM queues"
    before = [read(deployment.db, statement) for statement in [*kept, rest]]
    assert version(deployment.db) == 2 and len(before[0]) >= 6

    initialise(deployment.db, deployment.planted_db, upgrade=True)

    assert (version(deployment.db), version(deployment.planted_db)) == LATEST
    assert [read(deployment.db, statement) for statement in [*kept, rest]] == before
    stored = None if category is None else category.value
    assert read(
        deployment.db,
        "SELECT vital_adjusted_category, vital_adjusted_category_v2 FROM queues",
    ) == [(stored, stored)]
    assert read(deployment.db, "PRAGMA foreign_key_check") == []
    assert read(deployment.db, "PRAGMA integrity_check") == [("ok",)]
    result = deployment.verify()
    assert result.ok and result.head == head


@pytest.mark.parametrize("category", [RiskCategory.VERY_HIGH, RiskCategory.CRITICAL])
def test_an_upgraded_database_takes_the_new_categories(version_two, category):
    deployment = version_two()
    initialise(deployment.db, deployment.planted_db, upgrade=True)
    ov = deployment.open(config=with_category(deployment.config, category))
    [ticket, *_] = ov.submit(item(10))  # items still find their queue
    ov.shown(ticket.token, "r-1")
    ov.decided(ticket.token, "r-1", "reject")
    assert read(
        deployment.db,
        "SELECT vital_adjusted_category, vital_adjusted_category_v2 FROM queues",
    ) == [(category.value, "high")]  # the old column is no longer written
    assert deployment.verify().ok
    with closing(open_oversight_db(deployment.db)) as db:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute("UPDATE queues SET vital_adjusted_category = 'severe'")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            db.execute("DELETE FROM queues")


@pytest.mark.req("REQ-046")
def test_a_version_2_database_is_not_upgraded_unasked(version_two):
    deployment = version_two()
    before = deployment.db.read_bytes()
    with pytest.raises(StoreError, match="schema version 2.*Back up both.*rp init"):
        deployment.open()
    with pytest.raises(StoreError, match="rp init"):
        initialise(deployment.db, deployment.planted_db)
    assert deployment.db.read_bytes() == before
    assert version(deployment.db) == 2


def test_rp_init_upgrades_only_what_needs_it(version_two, capsys):
    deployment = version_two()
    upgrade_planted(deployment.planted_db).close()  # only oversight.db is now behind
    files = ["--db", str(deployment.db), "--planted", str(deployment.planted_db)]
    assert main(["init", *files]) == 0
    out = capsys.readouterr().out
    assert f"Upgraded {deployment.db}." in out
    assert f"Upgraded {deployment.planted_db}." not in out
    assert (version(deployment.db), version(deployment.planted_db)) == LATEST


def test_a_version_2_database_can_be_read_without_upgrading_it(version_two):
    deployment = version_two()
    before = deployment.db.read_bytes()
    assert (
        main(["verify", "--db", str(deployment.db), "--planted", str(deployment.planted_db)]) == 0
    )
    assert deployment.db.read_bytes() == before
    assert version(deployment.db) == 2


#: Changes a rating that was stored as "high".
DAMAGE = "UPDATE queues SET vital_adjusted_category = 'low'"


def damaged_copy(monkeypatch):
    """Make version 3 copy a rating wrongly."""
    third = list(schema.OVERSIGHT_MIGRATIONS[2])
    third.insert(third.index(schema._check_categories_copied), DAMAGE)
    monkeypatch.setattr(schema, "OVERSIGHT_MIGRATIONS", (*schema.OVERSIGHT_MIGRATIONS[:2], third))


@pytest.mark.req("REQ-046")
@pytest.mark.parametrize("category", [RiskCategory.HIGH, None])
def test_a_rating_that_is_not_copied_exactly_undoes_version_3(version_two, monkeypatch, category):
    # With no category too: a comparison that ignored empty values would miss that change.
    deployment = version_two(category)
    before = definition(deployment.db, "queues"), read(deployment.db, "SELECT * FROM queues")
    damaged_copy(monkeypatch)
    with pytest.raises(StoreError, match="not copied exactly"):
        upgrade_oversight(deployment.db)
    assert version(deployment.db) == 2
    assert (definition(deployment.db, "queues"), read(deployment.db, "SELECT * FROM queues")) == (
        before
    )


@pytest.mark.req("REQ-046")
def test_a_failure_in_version_3_undoes_version_2_as_well(version_one, monkeypatch):
    """From 0.1.0 both versions are applied as one upgrade: all of it, or none."""
    db_path, _, _ = version_one
    before = definition(db_path, "events"), events(db_path)
    third = [*schema.OVERSIGHT_MIGRATIONS[2], "NOT SQL"]
    monkeypatch.setattr(schema, "OVERSIGHT_MIGRATIONS", (*schema.OVERSIGHT_MIGRATIONS[:2], third))
    with pytest.raises(StoreError, match="cannot open"):
        upgrade_oversight(db_path)
    assert version(db_path) == 1
    assert (definition(db_path, "events"), events(db_path)) == before
    assert "vital_adjusted_category_v2" not in definition(db_path, "queues")


def test_new_and_upgraded_databases_have_the_same_queues_table(version_two, tmp_path):
    deployment = version_two()
    upgrade_oversight(deployment.db).close()
    create_oversight_db(tmp_path / "new.db").close()
    assert definition(deployment.db, "queues") == definition(tmp_path / "new.db", "queues")


# ----------------------------------------------------------------- version 4 (ADR-008)


@pytest.fixture
def version_three(tmp_path, monkeypatch):
    """Databases as the source had them before voiding: oversight.db 3, planted.db 2, with
    reviews of real and planted items.
    """
    (tmp_path / "v3").mkdir()
    with monkeypatch.context() as old:
        old.setattr(schema, "OVERSIGHT_MIGRATIONS", schema.OVERSIGHT_MIGRATIONS[:3])
        old.setattr(schema, "PLANTED_MIGRATIONS", schema.PLANTED_MIGRATIONS[:2])
        deployment = Deployment(tmp_path / "v3", 0.5)
        ov = deployment.open()
        deployment.load_planted(ov)
        for number in range(12):
            for ticket in ov.submit(item(number)):
                ov.shown(ticket.token, "r-1")
                ov.decided(ticket.token, "r-1", "approve")
        deployment.close()
    for path in (deployment.db, deployment.planted_db):
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    yield deployment
    deployment.close()


PLANTED_ROWS = "SELECT * FROM planted_errors ORDER BY planted_error_id"
REGISTRY_ROWS = "SELECT * FROM item_registry ORDER BY item_id"


@pytest.mark.req("REQ-046")
def test_version_4_copies_the_log_exactly_and_changes_no_record(version_three):
    deployment = version_three
    with closing(schema.open_readonly(deployment.db, "oversight")) as db:
        head = verify(db).head
    before = (
        events(deployment.db),
        read(deployment.planted_db, PLANTED_ROWS),
        read(deployment.planted_db, REGISTRY_ROWS),
    )
    assert (version(deployment.db), version(deployment.planted_db)) == (3, 2)

    initialise(deployment.db, deployment.planted_db, upgrade=True)

    assert (version(deployment.db), version(deployment.planted_db)) == LATEST
    assert (
        events(deployment.db),
        read(deployment.planted_db, PLANTED_ROWS),
        read(deployment.planted_db, REGISTRY_ROWS),
    ) == before
    result = deployment.verify()
    assert result.ok and result.head == head and result.voids == {}
    for path in (deployment.db, deployment.planted_db):
        assert read(path, "PRAGMA integrity_check") == [("ok",)]
        assert read(path, "PRAGMA foreign_key_check") == []


def test_an_upgraded_database_takes_a_void(version_three):
    deployment = version_three
    initialise(deployment.db, deployment.planted_db, upgrade=True)
    ov = deployment.open()
    [(target,)] = read(
        deployment.planted_db,
        "SELECT planted_error_id FROM item_registry WHERE kind = 'planted' LIMIT 1",
    )
    voids = ov.voids()
    voids.request(target, "qa-1", "not_defective", "why", "record")
    voids.confirm(target, "qa-lead")
    result = deployment.verify()
    assert result.ok and list(result.voids) == [target]


@pytest.mark.req("REQ-046")
def test_a_version_3_database_is_not_upgraded_unasked(version_three):
    deployment = version_three
    before = deployment.db.read_bytes(), deployment.planted_db.read_bytes()
    with pytest.raises(StoreError, match="schema version 3.*Back up both.*rp init"):
        deployment.open()
    with pytest.raises(StoreError, match="rp init"):
        initialise(deployment.db, deployment.planted_db)
    assert (deployment.db.read_bytes(), deployment.planted_db.read_bytes()) == before


def test_a_version_3_database_can_be_verified_and_reported_on(version_three, tmp_path):
    """planted.db has no void tables yet. Reading it must not need them."""
    deployment = version_three
    before = deployment.db.read_bytes(), deployment.planted_db.read_bytes()
    files = ["--db", str(deployment.db), "--planted", str(deployment.planted_db)]
    assert main(["verify", *files]) == 0
    settings = ["--config", str(deployment.folder / "reviewproof.yaml")]
    assert main(["report", *settings, *files, "--out", str(tmp_path / "r.html")]) == 0
    assert "Voided planted errors" not in (tmp_path / "r.html").read_text(encoding="utf-8")
    assert (deployment.db.read_bytes(), deployment.planted_db.read_bytes()) == before


@pytest.mark.req("REQ-046")
def test_a_log_not_copied_exactly_by_version_4_undoes_it(version_three, monkeypatch):
    deployment = version_three
    before = events(deployment.db), definition(deployment.db, "events")
    fourth = list(schema.OVERSIGHT_MIGRATIONS[3])
    fourth.insert(
        fourth.index(schema._check_log_copied_again),
        "UPDATE events_v4 SET system_id = 'altered' WHERE event_id = 3",
    )
    monkeypatch.setattr(schema, "OVERSIGHT_MIGRATIONS", (*schema.OVERSIGHT_MIGRATIONS[:3], fourth))
    with pytest.raises(StoreError, match="not copied exactly"):
        upgrade_oversight(deployment.db)
    assert version(deployment.db) == 3
    assert (events(deployment.db), definition(deployment.db, "events")) == before
    assert read(deployment.db, "SELECT 1 FROM sqlite_master WHERE name = 'events_v4'") == []
