"""``rp verify``: check the event log, and export its head for storage outside the tool."""

from __future__ import annotations

import argparse
import json
import re
import sys
from contextlib import closing, nullcontext
from pathlib import Path

from reviewproof.canonical import utc_now
from reviewproof.cli.common import EXIT_ERROR, EXIT_FAILED, EXIT_OK
from reviewproof.store.eventlog import Head, VerifyResult, verify
from reviewproof.store.schema import StoreError, open_readonly

_MAX_REGISTRY_LINES = 20
_HEAD_FORMAT = 1


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "verify",
        help="check that the event log has not been changed",
        description="Recompute the hash chain and report the first broken link. "
        "The databases are opened read-only.",
        epilog="Exit status: 0 verified, 1 verification failed, 2 could not run.",
    )
    parser.add_argument("--db", default="oversight.db", help="default: %(default)s")
    parser.add_argument(
        "--planted", help="planted.db, to also check the item registry against the log"
    )
    parser.add_argument(
        "--expect-head",
        metavar="FILE",
        help="a head exported earlier; detects removed events and a rebuilt database",
    )
    parser.add_argument(
        "--export-head",
        metavar="FILE",
        help="write the head to a new file, to keep somewhere this tool cannot write to",
    )
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    try:
        expected = read_head(Path(args.expect_head)) if args.expect_head else None
        planted_db = (
            closing(open_readonly(args.planted, "planted")) if args.planted else nullcontext()
        )
        with closing(open_readonly(args.db, "oversight")) as oversight, planted_db as planted:
            result = verify(oversight, planted, expected)
    except (StoreError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR

    _report(result, expected)
    if not result.ok:
        if args.export_head:
            print("The head was not exported, because verification failed.", file=sys.stderr)
        return EXIT_FAILED
    if args.export_head:
        try:
            _write_head(Path(args.export_head), result.head)
        except OSError as error:
            print(f"error: cannot export the head: {error}", file=sys.stderr)
            return EXIT_ERROR
        print(f"Head exported to {args.export_head}")
    return EXIT_OK


def _report(result: VerifyResult, expected: Head | None) -> None:
    if result.chain_error:
        print(f"FAILED: the chain is broken at event {result.broken_at}.")
        print(f"  {result.chain_error}")
        print(f"  Events before it that verify: {result.head.event_count}")
    else:
        print(f"Chain intact: {result.head.event_count} events.")
    print(f"Head hash: {result.head.head_hash}")

    if not result.registry_checked:
        print("Item registry: not checked (use --planted).")
    if result.registry_errors:
        print(f"FAILED: {len(result.registry_errors)} problem(s) with items.")
        for message in result.registry_errors[:_MAX_REGISTRY_LINES]:
            print(f"  {message}")
        hidden = len(result.registry_errors) - _MAX_REGISTRY_LINES
        if hidden > 0:
            print(f"  ... and {hidden} more")
    elif result.registry_checked:
        print("Item registry: matches the log.")

    if result.anchor_error:
        print("FAILED: the exported head check.")
        print(f"  {result.anchor_error}")
    elif expected is not None:
        print(f"Exported head: found at event {expected.event_count}.")
    else:
        print(
            "Note: without --expect-head, a rebuilt database or removed latest events "
            "cannot be detected."
        )


def read_head(path: Path) -> Head:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        if content["format_version"] != _HEAD_FORMAT:
            raise ValueError("unsupported format version")
        count, digest = content["event_count"], content["head_hash"]
        if type(count) is not int or count < 0 or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("bad event count or hash")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"cannot read the exported head {path}: {error}") from error
    return Head(count, digest)


def _write_head(path: Path, head: Head) -> None:
    content = {
        "format_version": _HEAD_FORMAT,
        "event_count": head.event_count,
        "head_hash": head.head_hash,
        "exported_at": utc_now(),
    }
    # "x": never overwrite. Replacing an earlier export would discard the evidence it anchors.
    with path.open("x", encoding="utf-8", newline="\n") as file:
        json.dump(content, file, indent=2)
        file.write("\n")
