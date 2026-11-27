"""Make three damaged COPIES of a pair of databases, to show that verification notices.

For test script MT-007. The originals are only read. Nothing is written outside the new folder
you name.

    python validation-pack/make_tampered_copies.py oversight.db planted.db NEW-FOLDER

In NEW-FOLDER:

    edited/       one recorded decision changed in the log
    truncated/    the latest events removed from the log
    relabelled/   one planted item relabelled as real in the restricted database

The copies hold everything the originals do, including planted errors: protect them the same
way, and delete them when the test is done.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

CASES = ("edited", "truncated", "relabelled")


def _copy(source: Path, target: Path) -> None:
    """A consistent copy, even while the source is in use. The source is opened read-only."""
    original = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    with closing(original), closing(sqlite3.connect(target)) as copy:
        original.backup(copy)


def _change(path: Path, trigger: str, statement: str) -> None:
    """Drop the protection, as someone with write access to the file could, then change a copy."""
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"DROP TRIGGER {trigger}")
        if db.execute(statement).rowcount < 1:
            raise SystemExit(f"nothing to change in {path.name}: use databases with reviews")


def make(oversight: Path, planted: Path, folder: Path) -> None:
    for source in (oversight, planted):
        if not source.is_file():
            raise SystemExit(f"no such database: {source}")
    if folder.exists():
        raise SystemExit(f"{folder} already exists: name a new folder")
    for case in CASES:
        (folder / case).mkdir(parents=True)
        _copy(oversight, folder / case / "oversight.db")
        _copy(planted, folder / case / "planted.db")
    _change(
        folder / "edited" / "oversight.db",
        "events_no_update",
        "UPDATE events SET decision = CASE decision WHEN 'reject' THEN 'approve' "
        "ELSE 'reject' END WHERE event_id = "
        "(SELECT MIN(event_id) FROM events WHERE type = 'decided')",
    )
    _change(
        folder / "truncated" / "oversight.db",
        "events_no_delete",
        "DELETE FROM events WHERE event_id > (SELECT MAX(event_id) - 5 FROM events)",
    )
    _change(
        folder / "relabelled" / "planted.db",
        "item_registry_no_update",
        "UPDATE item_registry SET kind = 'real', planted_error_id = NULL, "
        "assigned_reviewer = NULL WHERE item_id = "
        "(SELECT item_id FROM item_registry WHERE kind = 'planted' LIMIT 1)",
    )


def main(arguments: Sequence[str]) -> int:
    if len(arguments) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    make(*(Path(a) for a in arguments))
    print(f"Made {', '.join(CASES)} in {arguments[2]}. The originals were not changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
