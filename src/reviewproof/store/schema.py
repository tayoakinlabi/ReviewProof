"""Schema and migrations for ``oversight.db`` and ``planted.db`` (ADR-001).

Migrations only go forward. Once a release contains a migration, never edit it: add a new one.

The triggers stop changes made through normal SQL. Someone who can write to a database file can
still drop them, which is why the event log is also hash-chained and its head hash exported.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from reviewproof.canonical import utc_now

BUSY_TIMEOUT_MS = 5000

#: ``prev_hash`` of the first event.
GENESIS_HASH = "0" * 64

Migration = Sequence[str]


class StoreError(Exception):
    """A database is missing, unusable or not the expected one."""


def _is_hash(column: str) -> str:
    return f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'"


def _is_timestamp(column: str) -> str:
    digit = "[0-9]"
    d2, d4, d6 = digit * 2, digit * 4, digit * 6
    return f"{column} GLOB '{d4}-{d2}-{d2}T{d2}:{d2}:{d2}.{d6}Z'"


def _frozen_rows(table: str, *keys: str) -> list[str]:
    """Triggers that make rows permanent: no UPDATE, no DELETE, no replacing INSERT.

    The INSERT trigger matters. ``INSERT OR REPLACE`` deletes the old row without firing DELETE
    triggers unless a per-connection setting is on, so it has to be refused here.
    """
    clash = " OR ".join(f"{key} = NEW.{key}" for key in keys)
    return [
        f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT, '{table}: rows cannot be changed'); END""",
        f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT, '{table}: rows cannot be deleted'); END""",
        f"""CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table}
        WHEN EXISTS (SELECT 1 FROM {table} WHERE {clash})
        BEGIN SELECT RAISE(ABORT, '{table}: rows cannot be replaced'); END""",
    ]


# --------------------------------------------------------------------------- oversight.db

_OVERSIGHT_V1: Migration = [
    """CREATE TABLE systems (
        system_id         TEXT PRIMARY KEY NOT NULL,
        fingerprint       TEXT NOT NULL UNIQUE,
        name              TEXT NOT NULL,
        model_id          TEXT NOT NULL,
        model_version     TEXT NOT NULL,
        prompt_version    TEXT NOT NULL,
        tool_schema_hash  TEXT NOT NULL,
        intended_use      TEXT NOT NULL DEFAULT '',
        registered_at     TEXT NOT NULL
    )""",
    """CREATE TABLE queues (
        queue_id                 TEXT PRIMARY KEY NOT NULL,
        system_id                TEXT NOT NULL REFERENCES systems (system_id),
        task                     TEXT NOT NULL,
        planted_rate             REAL NOT NULL CHECK (planted_rate > 0 AND planted_rate <= 0.5),
        min_review_seconds       REAL NOT NULL CHECK (min_review_seconds >= 0),
        vital_base_risk          TEXT NOT NULL,
        vital_adjusted_category  TEXT CHECK (vital_adjusted_category IN ('low', 'medium', 'high')),
        gxp                      INTEGER NOT NULL CHECK (gxp IN (0, 1)),
        feedback                 TEXT NOT NULL CHECK (feedback IN ('immediate', 'batched')),
        site                     TEXT
    )""",
    """CREATE TABLE reviewers (
        reviewer_id           TEXT PRIMARY KEY NOT NULL,
        role                  TEXT,
        qualification_status  TEXT NOT NULL DEFAULT 'not_qualified' CHECK (
            qualification_status IN ('not_qualified', 'qualified', 'requalification_required'))
    )""",
    # No column here may differ between real and planted items. Kind and target ID are in
    # planted.db. Only a hash of the token is kept, so reading this file does not yield tokens.
    # system_id is the context the item came from: a queue moves on to later contexts.
    f"""CREATE TABLE items (
        item_id       TEXT PRIMARY KEY NOT NULL,
        queue_id      TEXT NOT NULL REFERENCES queues (queue_id),
        system_id     TEXT NOT NULL REFERENCES systems (system_id),
        token_hash    TEXT NOT NULL UNIQUE CHECK ({_is_hash("token_hash")}),
        payload_hash  TEXT NOT NULL CHECK ({_is_hash("payload_hash")}),
        created_at    TEXT NOT NULL CHECK ({_is_timestamp("created_at")})
    )""",
    *_frozen_rows("items", "item_id", "token_hash"),
    # No foreign keys: the log stands on its own.
    f"""CREATE TABLE events (
        event_id        INTEGER PRIMARY KEY NOT NULL,
        format_version  INTEGER NOT NULL,
        type            TEXT NOT NULL CHECK (type IN (
            'context_registered', 'submitted', 'shown', 'opened', 'edited', 'decided',
            'execute_requested', 'executed', 'execute_failed', 'blocked', 'withdrawn',
            'feedback_sent')),
        ts_utc          TEXT NOT NULL CHECK ({_is_timestamp("ts_utc")}),
        system_id       TEXT NOT NULL,
        queue_id        TEXT,
        item_id         TEXT,
        reviewer_id     TEXT,
        decision        TEXT CHECK (decision IN ('approve', 'edit', 'reject')),
        data            TEXT NOT NULL,
        prev_hash       TEXT NOT NULL CHECK ({_is_hash("prev_hash")}),
        hash            TEXT NOT NULL UNIQUE CHECK ({_is_hash("hash")})
    )""",
    "CREATE INDEX events_by_item ON events (item_id)",
    """CREATE TRIGGER events_no_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'events: the log is append-only'); END""",
    """CREATE TRIGGER events_no_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'events: the log is append-only'); END""",
    # An insert must extend the chain by exactly one link. This also refuses replacing inserts
    # and stops two writers from forking the chain. It does not recompute the hash.
    f"""CREATE TRIGGER events_extend_chain BEFORE INSERT ON events
    BEGIN
        SELECT RAISE(ABORT, 'events: event_id must be the next in sequence')
        WHERE NEW.event_id IS NOT (SELECT IFNULL(MAX(event_id), 0) + 1 FROM events);
        SELECT RAISE(ABORT, 'events: prev_hash must be the head hash')
        WHERE NEW.prev_hash IS NOT IFNULL(
            (SELECT hash FROM events ORDER BY event_id DESC LIMIT 1), '{GENESIS_HASH}');
        SELECT RAISE(ABORT, 'events: hash is already in the log')
        WHERE EXISTS (SELECT 1 FROM events WHERE hash = NEW.hash);
    END""",
    f"""CREATE TABLE qualifications (
        session_id   TEXT PRIMARY KEY NOT NULL,
        reviewer_id  TEXT NOT NULL REFERENCES reviewers (reviewer_id),
        system_id    TEXT NOT NULL REFERENCES systems (system_id),
        queue_id     TEXT NOT NULL REFERENCES queues (queue_id),
        score        REAL NOT NULL CHECK (score >= 0 AND score <= 1),
        passed       INTEGER NOT NULL CHECK (passed IN (0, 1)),
        ts_utc       TEXT NOT NULL CHECK ({_is_timestamp("ts_utc")})
    )""",
    f"""CREATE TABLE review_context (
        context_id     INTEGER PRIMARY KEY,
        item_id        TEXT NOT NULL REFERENCES items (item_id),
        reviewer_id    TEXT NOT NULL REFERENCES reviewers (reviewer_id),
        shown_sources  TEXT NOT NULL,
        shown_steps    TEXT NOT NULL,
        shown_fields   TEXT NOT NULL,
        manifest_hash  TEXT NOT NULL CHECK ({_is_hash("manifest_hash")})
    )""",
    f"""CREATE TABLE traces (
        item_id       TEXT NOT NULL REFERENCES items (item_id),
        step_id       TEXT NOT NULL,
        step_type     TEXT NOT NULL,
        inputs_hash   TEXT CHECK ({_is_hash("inputs_hash")}),
        outputs_hash  TEXT CHECK ({_is_hash("outputs_hash")}),
        ts_utc        TEXT CHECK ({_is_timestamp("ts_utc")}),
        PRIMARY KEY (item_id, step_id)
    )""",
]

# --------------------------------------------------------------------------- planted.db

_PLANTED_CONTENT = (
    "planted_error_id",
    "queue_id",
    "defect_class",
    "defect_field",
    "expected_decision",
    "payload",
    "correct_value",
    "notes",
    "reserved_target_id",
    "author",
    "imported_at",
)

_PLANTED_SET_ONCE = (
    "approved_by",
    "approved_at",
    "second_approved_by",
    "second_approved_at",
    "retired_at",
    "retired_by",
    "retire_reason",
)


def _person(column: str) -> str:
    """Compare names without regard to case or surrounding spaces."""
    return f"lower(trim({column}))"


_PLANTED_V1: Migration = [
    f"""CREATE TABLE planted_errors (
        planted_error_id    TEXT PRIMARY KEY NOT NULL,
        queue_id            TEXT NOT NULL,
        defect_class        TEXT NOT NULL CHECK (defect_class IN (
            'wrong_value', 'omission', 'wrong_classification', 'unsupported_claim',
            'wrong_target', 'policy_breach')),
        defect_field        TEXT NOT NULL,
        expected_decision   TEXT NOT NULL,
        payload             TEXT NOT NULL,
        correct_value       TEXT,
        notes               TEXT,
        reserved_target_id  TEXT NOT NULL UNIQUE,
        author              TEXT NOT NULL CHECK (trim(author) <> ''),
        imported_at         TEXT NOT NULL CHECK ({_is_timestamp("imported_at")}),
        approved_by         TEXT CHECK (trim(approved_by) <> ''),
        approved_at         TEXT CHECK ({_is_timestamp("approved_at")}),
        second_approved_by  TEXT CHECK (trim(second_approved_by) <> ''),
        second_approved_at  TEXT CHECK ({_is_timestamp("second_approved_at")}),
        retired_at          TEXT CHECK ({_is_timestamp("retired_at")}),
        retired_by          TEXT CHECK (trim(retired_by) <> ''),
        retire_reason       TEXT,
        CHECK ((retired_at IS NULL) = (retired_by IS NULL)),
        CHECK (retire_reason IS NULL OR retired_at IS NOT NULL),
        CHECK (expected_decision = 'reject' OR expected_decision = 'edit:' || defect_field),
        CHECK ((approved_by IS NULL) = (approved_at IS NULL)),
        CHECK ((second_approved_by IS NULL) = (second_approved_at IS NULL)),
        CHECK (second_approved_by IS NULL OR approved_by IS NOT NULL),
        CHECK ({_person("approved_by")} <> {_person("author")}),
        CHECK ({_person("second_approved_by")} <> {_person("author")}),
        CHECK ({_person("second_approved_by")} <> {_person("approved_by")})
    )""",
    """CREATE TRIGGER planted_errors_start_unapproved BEFORE INSERT ON planted_errors
    WHEN NEW.approved_by IS NOT NULL OR NEW.second_approved_by IS NOT NULL
        OR NEW.retired_at IS NOT NULL OR NEW.retired_by IS NOT NULL
    BEGIN SELECT RAISE(ABORT, 'planted_errors: new rows must be unapproved'); END""",
    """CREATE TRIGGER planted_errors_no_replace BEFORE INSERT ON planted_errors
    WHEN EXISTS (SELECT 1 FROM planted_errors
                 WHERE planted_error_id = NEW.planted_error_id
                    OR reserved_target_id = NEW.reserved_target_id)
    BEGIN SELECT RAISE(ABORT, 'planted_errors: rows cannot be replaced'); END""",
    """CREATE TRIGGER planted_errors_no_delete BEFORE DELETE ON planted_errors
    BEGIN SELECT RAISE(ABORT, 'planted_errors: retire a planted error instead'); END""",
    # An approval only means something if what was approved cannot change afterwards.
    f"""CREATE TRIGGER planted_errors_content_fixed
    BEFORE UPDATE OF {", ".join(_PLANTED_CONTENT)} ON planted_errors
    BEGIN SELECT RAISE(ABORT, 'planted_errors: content cannot be changed; import a new one');
    END""",
    *[
        f"""CREATE TRIGGER planted_errors_{column}_set_once
        BEFORE UPDATE OF {column} ON planted_errors
        WHEN OLD.{column} IS NOT NULL AND NEW.{column} IS NOT OLD.{column}
        BEGIN SELECT RAISE(ABORT, 'planted_errors: {column} cannot be changed once set'); END"""
        for column in _PLANTED_SET_ONCE
    ],
    # One row for every item, real or planted. Target IDs are here and not in oversight.db,
    # because a reserved-range ID would show which items are planted.
    """CREATE TABLE item_registry (
        item_id           TEXT PRIMARY KEY NOT NULL,
        kind              TEXT NOT NULL CHECK (kind IN ('real', 'planted')),
        planted_error_id  TEXT REFERENCES planted_errors (planted_error_id),
        target_id         TEXT NOT NULL CHECK (trim(target_id) <> ''),
        salt              BLOB NOT NULL CHECK (length(salt) = 32),
        payload_salt      BLOB NOT NULL CHECK (length(payload_salt) = 32),
        CHECK ((kind = 'planted') = (planted_error_id IS NOT NULL))
    )""",
    *_frozen_rows("item_registry", "item_id"),
]

OVERSIGHT_MIGRATIONS: tuple[Migration, ...] = (_OVERSIGHT_V1,)
PLANTED_MIGRATIONS: tuple[Migration, ...] = (_PLANTED_V1,)


# --------------------------------------------------------------------------- opening


def create_oversight_db(path: str | Path) -> sqlite3.Connection:
    """Create ``oversight.db`` if needed, bring it up to date and open it."""
    return _open(path, "oversight", OVERSIGHT_MIGRATIONS, create=True)


def create_planted_db(path: str | Path) -> sqlite3.Connection:
    """Create ``planted.db`` if needed, bring it up to date and open it."""
    return _open(path, "planted", PLANTED_MIGRATIONS, create=True)


def open_oversight_db(path: str | Path) -> sqlite3.Connection:
    """Open an existing ``oversight.db``. A missing file is an error."""
    return _open(path, "oversight", OVERSIGHT_MIGRATIONS, create=False)


def open_planted_db(path: str | Path) -> sqlite3.Connection:
    """Open an existing ``planted.db``. A missing file is an error.

    At run time the registry is never created on demand: a lost registry must stop execution,
    not be quietly replaced by an empty one.
    """
    return _open(path, "planted", PLANTED_MIGRATIONS, create=False)


def open_readonly(path: str | Path, component: str) -> sqlite3.Connection:
    """Open an existing database without changing it, for verification and audit.

    No migration is run. ``component`` is ``"oversight"`` or ``"planted"``.
    """
    migrations = {"oversight": OVERSIGHT_MIGRATIONS, "planted": PLANTED_MIGRATIONS}[component]
    path = Path(path).resolve()
    connection = None
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        _check_identity(connection, component, may_initialise=False)
        _check_not_newer(_current_version(connection), component, migrations)
    except sqlite3.Error as error:
        _close(connection)
        raise StoreError(f"cannot open {component} database at {path}: {error}") from error
    except BaseException:
        _close(connection)
        raise
    return connection


def _check_not_newer(version: int, component: str, migrations: Sequence[Migration]) -> None:
    if version > len(migrations):
        raise StoreError(
            f"{component} database is at schema version {version}, newer than this "
            f"release supports ({len(migrations)}). Upgrade ReviewProof."
        )


def _open(
    path: str | Path, component: str, migrations: Sequence[Migration], create: bool
) -> sqlite3.Connection:
    path = Path(path).resolve()
    mode = "rwc" if create else "rw"
    connection = None
    try:
        # isolation_level=None: transactions are started and ended explicitly.
        connection = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA recursive_triggers = ON")
        _check_identity(connection, component, may_initialise=create)
        journal = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if journal.lower() != "wal":
            raise StoreError(f"{path}: could not switch to WAL mode (got {journal!r})")
        connection.execute("PRAGMA synchronous = FULL")
        _migrate(connection, component, migrations, may_initialise=create)
    except sqlite3.Error as error:
        _close(connection)
        raise StoreError(f"cannot open {component} database at {path}: {error}") from error
    except BaseException:
        _close(connection)
        raise
    return connection


def _close(connection: sqlite3.Connection | None) -> None:
    if connection is not None:
        connection.close()


def _check_identity(connection: sqlite3.Connection, component: str, may_initialise: bool) -> None:
    """Refuse the wrong file before anything in it is changed."""
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not tables:
        if may_initialise:
            return
        raise StoreError(f"not a {component} database: it is empty")
    if "schema_version" not in tables:
        raise StoreError(f"not a {component} database: it holds other data")
    found = {row[0] for row in connection.execute("SELECT component FROM schema_version")}
    if found != {component}:
        listed = ", ".join(sorted(found)) or "an unknown one"
        raise StoreError(f"expected a {component} database, found: {listed}")


def _migrate(
    connection: sqlite3.Connection,
    component: str,
    migrations: Sequence[Migration],
    may_initialise: bool,
) -> None:
    """Apply pending migrations in one transaction: all of them, or none."""
    if _current_version(connection) == len(migrations):
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        # Checked again under the write lock: another process may have migrated meanwhile.
        _check_identity(connection, component, may_initialise)
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_version (
                component   TEXT NOT NULL,
                version     INTEGER NOT NULL,
                applied_at  TEXT NOT NULL,
                PRIMARY KEY (component, version)
            )"""
        )
        version = _current_version(connection)
        _check_not_newer(version, component, migrations)
        for number, statements in enumerate(migrations[version:], start=version + 1):
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_version (component, version, applied_at) VALUES (?, ?, ?)",
                (component, number, utc_now()),
            )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _current_version(connection: sqlite3.Connection) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if not exists:
        return 0
    return connection.execute("SELECT IFNULL(MAX(version), 0) FROM schema_version").fetchone()[0]
