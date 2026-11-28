"""Read records from the databases that the ``rp`` commands do not show. For QA.

    python validation-pack/planted_records.py register planted.db

Every planted error: who wrote, approved and retired it, when, and why, and for one drafted
from real output, who confirmed the original and who checked it for real data. It shows no
content.

    python validation-pack/planted_records.py uses PE-0001 oversight.db planted.db

Everyone known to have been given, shown or to have decided on a copy of one planted error,
in live review or in a qualification session. For a live copy with nothing recorded, the
reviewer is blank unless your application named one. Use it when a planted error turns out to
be wrong or recognisable, because retiring it leaves those results in every score.

    python validation-pack/planted_records.py voids oversight.db planted.db

Every request to void a planted error: what became of it, why, and who asked, confirmed or
cancelled it. A void counts only if its state is ``in_effect``, which comes from the log.

Both databases are opened read-only. **This does not check that the records are unchanged:** run
``rp verify --planted planted.db`` first. Verification does not cover retirement details.

The output is confidential. It identifies planted errors, and ``uses`` also shows what
individual reviewers did.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from reviewproof.feedback import score
from reviewproof.store.eventlog import voids_in_effect
from reviewproof.store.schema import StoreError, open_readonly
from reviewproof.store.void_check import read_voids
from reviewproof.voids import state_of

REGISTER = (
    "planted_error_id",
    "queue_id",
    "purpose",
    "defect_class",
    "author",
    "imported_at",
    "approved_by",
    "approved_at",
    "second_approved_by",
    "second_approved_at",
    "retired_by",
    "retired_at",
    "retire_reason",
    "origin",
    "source_confirmed_by",
    "checked_for_real_data_by",
)
USES = ("time_utc", "use", "reference", "reviewer", "what_happened", "decision", "scored_as")

VOIDS = (
    "planted_error_id",
    "state",
    "reason",
    "explanation",
    "reference",
    "requested_by",
    "requested_at",
    "confirmed_by",
    "confirmed_at",
    "cancelled_by",
    "cancelled_at",
)

_EVENTS = (
    "SELECT event_id, ts_utc, type, reviewer_id, decision, data FROM events "
    "WHERE item_id = ? AND type IN "
    "('shown', 'decided', 'feedback_sent', 'qualification_answered') ORDER BY event_id"
)


#: The last columns of the register come from the provenance of one drafted from real output.
_FROM_PROVENANCE = ("origin", "source_confirmed_by", "checked_for_real_data_by")


def register(planted: Path) -> list[tuple]:
    with closing(open_readonly(planted, "planted")) as db:
        columns = ", ".join(REGISTER[: -len(_FROM_PROVENANCE)])
        present = {row[1] for row in db.execute("PRAGMA table_info(planted_errors)")}
        # A database not yet upgraded has no provenance.
        provenance = "provenance" if "provenance" in present else "NULL"
        rows = []
        for row in db.execute(f"SELECT {columns}, {provenance} FROM planted_errors ORDER BY rowid"):
            origin = json.loads(row[-1]) if row[-1] else {}
            rows.append((*tuple(row)[:-1], *(origin.get(name) for name in _FROM_PROVENANCE)))
        return rows


def voids(oversight: Path, planted: Path) -> list[tuple]:
    with (
        closing(open_readonly(oversight, "oversight")) as log,
        closing(open_readonly(planted, "planted")) as db,
    ):
        in_effect = voids_in_effect(log, db)
        return [
            tuple(
                state_of(void, in_effect).value
                if name == "state"
                else getattr(void, name).value
                if name == "reason"
                else getattr(void, name)
                for name in VOIDS
            )
            for void in read_voids(db)
        ]


def uses(planted_error_id: str, oversight: Path, planted: Path) -> list[tuple]:
    with (
        closing(open_readonly(oversight, "oversight")) as log,
        closing(open_readonly(planted, "planted")) as db,
    ):
        found = db.execute(
            "SELECT defect_field FROM planted_errors WHERE planted_error_id = ?",
            (planted_error_id,),
        ).fetchone()
        if found is None:
            raise StoreError(f"no planted error {planted_error_id!r}")
        defect_field = found[0]
        # Each copy: how it was used, its item, what to call it, and who it was given to.
        copies = [
            ("live", item_id, item_id, assigned)
            for item_id, assigned in db.execute(
                "SELECT item_id, assigned_reviewer FROM item_registry WHERE planted_error_id = ?",
                (planted_error_id,),
            )
        ]
        in_sessions = db.execute(
            "SELECT item_id, session_id FROM qualification_items WHERE planted_error_id = ?",
            (planted_error_id,),
        ).fetchall()
        if in_sessions:
            # A whole session goes to one person, so they had it whether or not they answered.
            sat_by = {
                json.loads(row["data"])["session_id"]: row["reviewer_id"]
                for row in log.execute(
                    "SELECT reviewer_id, data FROM events WHERE type = 'qualification_started'"
                )
            }
            copies += [("qualification", i, s, sat_by.get(s)) for i, s in in_sessions]

        rows = []
        for use, item_id, reference, given_to in copies:
            events = log.execute(_EVENTS, (item_id,)).fetchall()
            if not events:
                rows.append((0, "", use, reference, given_to or "", "nothing recorded", "", ""))
            for event_id, at, kind, reviewer, decision, data in events:
                outcome = ""
                if decision:
                    edited = json.loads(data).get("edited_fields", [])
                    outcome = score(decision, edited, defect_field).value
                rows.append((event_id, at, use, reference, reviewer, kind, decision or "", outcome))
        return [row[1:] for row in sorted(rows)]


def main(arguments: Sequence[str]) -> int:
    try:
        match list(arguments):
            case ["register", planted]:
                header, rows = REGISTER, register(Path(planted))
            case ["voids", oversight, planted]:
                header, rows = VOIDS, voids(Path(oversight), Path(planted))
            case ["uses", planted_error_id, oversight, planted]:
                header = USES
                rows = uses(planted_error_id, Path(oversight), Path(planted))
            case _:
                print(__doc__, file=sys.stderr)
                return 2
    except (StoreError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(_safe(value) for value in row)
    return 0


def _safe(value: object) -> object:
    """A spreadsheet treats a cell starting with one of these as a formula."""
    text = "" if value is None else value
    if isinstance(text, str) and text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
