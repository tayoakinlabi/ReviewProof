"""Shared helpers for the event log tests."""

import contextlib

from reviewproof.models import Decision, EventType, ItemKind
from reviewproof.store.commitments import new_salt, planted_commitment

TS = "2026-11-26T10:00:00.000000Z"


@contextlib.contextmanager
def triggers_removed(db, table="events"):
    """Tamper as someone with write access to the file would: drop, edit, put back."""
    saved = db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?", (table,)
    ).fetchall()
    for name, _ in saved:
        db.execute(f"DROP TRIGGER {name}")
    try:
        yield db
    finally:
        for _, sql in saved:
            db.execute(sql)


def add_item(log, registry, item_id, kind=ItemKind.REAL, planted_error_id=None):
    """Register an item and log its submission, as the API will (ADR-001: registry first)."""
    salt, target = new_salt(), f"target-{item_id}"
    registry.execute(
        "INSERT INTO item_registry (item_id, kind, planted_error_id, target_id, salt, "
        "payload_salt) VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, kind.value, planted_error_id, target, salt, new_salt()),
    )
    commitment = planted_commitment(item_id, kind, planted_error_id, target, salt)
    return log.append(
        EventType.SUBMITTED, system_id="sys-1", item_id=item_id, data={"commitment": commitment}
    )


def add_planted_error(registry, planted_error_id="PE-1"):
    registry.execute(
        "INSERT INTO planted_errors (planted_error_id, queue_id, defect_class, defect_field, "
        "expected_decision, payload, reserved_target_id, author, imported_at) "
        "VALUES (?, 'q', 'omission', 'f', 'reject', '{}', ?, 'author-1', ?)",
        (planted_error_id, f"rp-reserved-{planted_error_id}", TS),
    )


def fill(log, count=5):
    for number in range(1, count + 1):
        log.append(
            EventType.DECIDED,
            system_id="sys-1",
            queue_id="q",
            item_id=f"item-{number}",
            reviewer_id="r-1",
            decision=Decision.APPROVE,
            data={"dwell_ms": number * 1000},
        )
