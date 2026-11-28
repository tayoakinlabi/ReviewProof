"""``rp craft``: draft planted errors from an export of real agent output (ADR-009).

These commands open no database. Their messages never repeat a value from your files.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from reviewproof import craft
from reviewproof.cli import common
from reviewproof.config import Config, ConfigError, load_config


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "craft",
        help="draft planted errors from an export of real agent output",
        description="Draft planted errors from items you export. Real data is handled: run "
        "these where reviewers cannot see, under your data protection rules. This cannot make "
        "an item anonymous. See the authoring guide.",
    )
    actions = parser.add_subparsers(title="actions", metavar="ACTION", required=True)

    def action(name: str, help_text: str, run: Callable):
        added = actions.add_parser(
            name,
            help=help_text,
            description=help_text.capitalize() + ".",
            epilog="Exit status: 0 done, 2 refused or could not run.",
        )
        common.add_settings(added)
        added.add_argument("--profile", required=True, metavar="FILE", help="handling profile")
        added.add_argument(
            "--export", required=True, metavar="FILE", help="a .json list of exported items"
        )
        added.set_defaults(run=_guarded(run))
        return added

    starter = action("start", "make a working file from one exported item", _start)
    starter.add_argument("--item", required=True, type=int, help="which item, from 0")
    starter.add_argument("--id", required=True, dest="planted_error_id")
    starter.add_argument("--author", required=True, help="a pseudonymous ID")
    starter.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        dest="values",
        help="a new value for a replaced property; repeat for several",
    )
    starter.add_argument("--shift-days", type=int, help="move every shifted date by this")
    starter.add_argument("--out", required=True, metavar="FILE", help="never overwritten")

    defect = action("defect", "add one defect to a copy of a working file", _defect)
    defect.add_argument("--work", required=True, metavar="FILE")
    defect.add_argument("--mutator", required=True, choices=craft.MUTATORS)
    defect.add_argument("--field", required=True, help="the property to change")
    defect.add_argument("--new-value", help="value-swap: the wrong value (JSON unless text)")
    defect.add_argument("--remove-index", type=int, help="omission: which list entry, from 0")
    defect.add_argument("--to", help="classification-flip: the wrong class")
    defect.add_argument("--out", required=True, metavar="FILE", help="never overwritten")

    checker = action("check", "show what stops a draft being finished", _check)
    checker.add_argument("--work", required=True, metavar="FILE")

    finisher = action("finish", "write the planted-error file once every check passes", _finish)
    finisher.add_argument("--work", required=True, metavar="FILE")
    finisher.add_argument(
        "--source-confirmed-by",
        required=True,
        help="who confirmed that the original was correct; not the author; not verified",
    )
    finisher.add_argument(
        "--checked-for-real-data-by",
        required=True,
        help="who read the whole finished item; not the author; not verified",
    )
    finisher.add_argument(
        "--acknowledge", action="append", default=[], metavar="CODE", help="a warning read"
    )
    finisher.add_argument("--out", required=True, metavar="FILE", help="never overwritten")


def _guarded(run: Callable) -> Callable:
    def wrapper(args: argparse.Namespace) -> int:
        try:
            config = load_config(args.config)
            profile = craft.load_profile(args.profile, config)
            export = craft.load_export(args.export, config, profile)
            return run(args, config, profile, export)
        except (craft.CraftError, ConfigError) as error:
            print(f"error: {error}", file=sys.stderr)
        except FileExistsError:
            print("error: the output file already exists; nothing was overwritten", file=sys.stderr)
        except Exception as error:
            # A traceback or an unexpected message could repeat real data.
            print(
                f"error: could not be done ({type(error).__name__}). No details are shown, "
                "because they could repeat values from your files.",
                file=sys.stderr,
            )
        return common.EXIT_ERROR

    return wrapper


def _start(args: argparse.Namespace, config: Config, profile, export) -> int:
    values = {}
    for entry in args.values:
        name, equals, value = entry.partition("=")
        if not equals or not name or name in values:
            raise craft.CraftError("give each new value once, as --set NAME=VALUE")
        values[name] = value
    working = craft.start(
        config,
        profile,
        export,
        args.item,
        planted_error_id=args.planted_error_id,
        author=args.author,
        new_values=values,
        shift_days=args.shift_days,
    )
    craft.write_working(working, args.out)
    print(f"Working file written to {args.out}. IT MAY STILL HOLD REAL INFORMATION.")
    print("Add one defect, then run rp craft check.")
    return common.EXIT_OK


def _value(text: str, current: Any) -> Any:
    """Text for a text property; otherwise JSON, such as ``true`` or ``12.5``."""
    if isinstance(current, str):
        return text
    try:
        return json.loads(text)
    except ValueError:
        return text


def _defect(args: argparse.Namespace, config: Config, profile, export) -> int:
    working = craft.load_working(args.work)
    payload = working["payload"] if isinstance(working["payload"], dict) else {}
    current = payload.get(args.field)
    if args.mutator == "value-swap" and args.new_value is None:
        raise craft.CraftError("value-swap needs --new-value")
    changed = craft.add_defect(
        config,
        profile,
        export,
        working,
        args.mutator,
        args.field,
        new_value=None if args.new_value is None else _value(args.new_value, current),
        index=args.remove_index,
        to=None if args.to is None else _value(args.to, current),
    )
    craft.write_working(changed, args.out)
    print(f"Written to {args.out}, which may still hold real information. {args.work} is kept.")
    print("Reword any text that no longer fits, then run rp craft check.")
    return common.EXIT_OK


def _check(args: argparse.Namespace, config: Config, profile, export) -> int:
    report = craft.check(config, profile, export, craft.load_working(args.work))
    if report.changed:
        print(f"Differs from the original in: {', '.join(report.changed)}")
    for block in report.blocks:
        print(f"STOPS IT  {block}")
    for warning in report.warnings:
        print(f"WARNING   {warning.code}  {warning.message}")
    if report.blocks:
        print("It cannot be finished yet.")
        return common.EXIT_ERROR
    print(
        "Nothing stops it. This does not show that it holds no real information: someone "
        "must read the whole item."
    )
    return common.EXIT_OK


def _finish(args: argparse.Namespace, config: Config, profile, export) -> int:
    data = craft.finish(
        config,
        profile,
        export,
        craft.load_working(args.work),
        source_confirmed_by=args.source_confirmed_by,
        checked_for_real_data_by=args.checked_for_real_data_by,
        acknowledged=args.acknowledge,
    )
    craft.write_finished(data, args.out)
    print(f"Planted-error file written to {args.out}. Import it, then have it approved.")
    print(
        f"The export and {args.work} may hold real information. This tool does not delete "
        "them: destroy them as your procedure says."
    )
    return common.EXIT_OK
