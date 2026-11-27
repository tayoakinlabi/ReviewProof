"""What ``rp verify`` checks for qualification sessions (ADR-006)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from reviewproof.models import Decision, EventType
from reviewproof.store.commitments import judgment_hash, qualification_commitment
from reviewproof.store.registry import fingerprint_of
from reviewproof.store.schema import StoreError

_MISMATCH = "the session records do not match the log"
_RESULT = "the session result does not match the answers"


def check_qualification(
    planted: sqlite3.Connection, events: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Check each session's items and judgments against what the log committed to, and work
    each result out again from the logged answers.

    ``events`` are the qualification events of a verified chain, in order, with data parsed.
    """
    sessions: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        sessions.setdefault(str(event["data"].get("session_id")), []).append(event)
    errors = []
    for session_id, history in sessions.items():
        where = f"event {history[0]['event_id']}, session {session_id}"
        try:
            problem = _check_session(planted, history)
        except (StoreError, sqlite3.Error):
            problem = "the session records cannot be read"
        except (KeyError, TypeError, ValueError, AttributeError):
            problem = _MISMATCH
        if problem:
            errors.append(f"{where}: {problem}")
    return errors


def _check_session(planted: sqlite3.Connection, history: Sequence[Mapping[str, Any]]) -> str | None:
    started = history[0]
    if started["type"] != EventType.QUALIFICATION_STARTED.value:
        return "the session has no start"
    committed = started["data"]["items"]
    key: dict[str, str | None] = {}
    salts: dict[str, bytes] = {}
    for position, entry in enumerate(committed, start=1):
        row = planted.execute(
            "SELECT session_id, position, planted_error_id, payload, salt "
            "FROM qualification_items WHERE item_id = ?",
            (entry["item_id"],),
        ).fetchone()
        if row is None or row[0] != started["data"]["session_id"] or row[1] != position:
            return _MISMATCH
        fingerprint = None if row[2] is None else fingerprint_of(planted, row[2])
        actual = qualification_commitment(
            entry["item_id"], row[0], row[1], row[2], row[3], row[4], fingerprint
        )
        if actual != entry["commitment"]:
            return _MISMATCH
        key[entry["item_id"]], salts[entry["item_id"]] = row[2], row[4]

    answers: dict[str, Mapping[str, Any]] = {}
    ended = None
    for event in history[1:]:
        kind, item_id = event["type"], event["item_id"]
        if ended is not None or event["reviewer_id"] != started["reviewer_id"]:
            return "the session has entries that do not belong to it"
        if kind == EventType.QUALIFICATION_FINISHED.value:
            ended = event["data"]
        elif item_id not in key:
            return "the session has entries that do not belong to it"
        elif kind == EventType.QUALIFICATION_ANSWERED.value:
            if item_id in answers:
                return "an item was answered twice"
            answers[item_id] = event
        elif kind == EventType.QUALIFICATION_JUDGED.value:
            row = planted.execute(
                "SELECT judgment FROM qualification_judgments WHERE item_id = ?", (item_id,)
            ).fetchone()
            if (
                row is None
                or judgment_hash(row[0], salts[item_id]) != (event["data"]["judgment_hash"])
            ):
                return _MISMATCH
    if ended is None:
        return None
    return _check_result(planted, started["data"]["criteria"], key, answers, ended)


def _check_result(
    planted: sqlite3.Connection,
    criteria: Mapping[str, int],
    key: Mapping[str, str | None],
    answers: Mapping[str, Mapping[str, Any]],
    ended: Mapping[str, Any],
) -> str | None:
    caught = defects = false_rejects = correct = 0
    for item_id, planted_error_id in key.items():
        answer = answers.get(item_id)
        if planted_error_id is None:
            correct += 1
            false_rejects += bool(answer) and answer["decision"] != Decision.APPROVE.value
            continue
        defects += 1
        if answer is None:
            continue
        row = planted.execute(
            "SELECT defect_field FROM planted_errors WHERE planted_error_id = ?",
            (planted_error_id,),
        ).fetchone()
        edited = json.loads(json.dumps(answer["data"])).get("edited_fields", [])
        caught += answer["decision"] == Decision.REJECT.value or (
            answer["decision"] == Decision.EDIT.value and row[0] in edited
        )
    abandoned = ended["abandoned"]
    if not abandoned and len(answers) != len(key):
        return _RESULT
    passed = (
        not abandoned
        and caught >= criteria["min_caught"]
        and false_rejects <= criteria["max_false_rejects"]
    )
    expected = (passed, caught, defects, false_rejects, correct)
    logged = tuple(
        ended[name] for name in ("passed", "caught", "defects", "false_rejects", "correct_items")
    )
    return None if expected == logged else _RESULT
