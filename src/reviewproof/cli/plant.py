"""``rp plant``: manage the planted-error library. For QA administrators."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from reviewproof import mutators
from reviewproof.api import Oversight
from reviewproof.cli import common
from reviewproof.config import Config, ConfigError, load_config
from reviewproof.errors import OversightError
from reviewproof.itemschema import SchemaError
from reviewproof.models import VoidReason
from reviewproof.planted import (
    ApprovalError,
    PlantedError,
    PlantedImportError,
    PlantedLibrary,
    Status,
    UnknownPlantedError,
    approvals_needed,
)
from reviewproof.store.eventlog import voids_in_effect
from reviewproof.store.schema import StoreError, open_planted_db, open_readonly
from reviewproof.store.void_check import read_voids
from reviewproof.strictload import LoadError, load_file
from reviewproof.voids import VoidState, state_of

_ERRORS = (
    OversightError,
    ConfigError,
    StoreError,
    SchemaError,
    LoadError,
    PlantedImportError,
    ApprovalError,
    UnknownPlantedError,
    mutators.DraftError,
    OSError,
    ValueError,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    plant = subparsers.add_parser(
        "plant",
        help="import, approve and retire planted errors",
        description="Manage planted errors. They are secret: run these where reviewers cannot "
        "see. See the authoring guide.",
    )
    actions = plant.add_subparsers(title="actions", metavar="ACTION", required=True)

    def action(name: str, help_text: str, run: Callable, database: bool = True):
        parser = actions.add_parser(
            name,
            help=help_text,
            description=help_text.capitalize() + ".",
            epilog="Exit status: 0 done, 2 refused or could not run.",
        )
        common.add_settings(parser)
        if database:
            parser.add_argument("--planted", default="planted.db", help="default: %(default)s")
        parser.set_defaults(run=_guarded(run))
        return parser

    importer = action("import", "check files and store them, not yet approved", _import)
    importer.add_argument("files", nargs="+", metavar="FILE", help="YAML or JSON files")

    approver = action("approve", "record one approval", _approve)
    approver.add_argument("planted_error_id", metavar="ID")
    approver.add_argument(
        "--approver", required=True, help="who is approving; not verified by ReviewProof"
    )

    retire = action("retire", "stop using a planted error, permanently", _retire)
    retire.add_argument("planted_error_id", metavar="ID")
    retire.add_argument("--by", required=True, help="who is retiring it")
    retire.add_argument("--reason", help="for example: a reviewer suspected it")

    def void_action(name: str, help_text: str, run: Callable):
        parser = action(name, help_text, run)
        parser.add_argument("planted_error_id", metavar="ID")
        parser.add_argument("--db", default="oversight.db", help="default: %(default)s")
        parser.add_argument("--by", required=True, help="who is doing this; not verified")
        return parser

    voider = void_action(
        "void", "ask to void a faulty planted error; nothing changes until confirmed", _void
    )
    voider.add_argument("--reason", required=True, choices=[r.value for r in VoidReason])
    voider.add_argument("--explanation", required=True, help="what is wrong with it")
    voider.add_argument("--reference", required=True, help="your quality record")
    void_action(
        "void-confirm",
        "confirm someone else's request: all its results leave the scores, permanently",
        _void_confirm,
    )
    void_action("void-cancel", "drop a request that is not in effect", _void_cancel)

    lister = action("list", "list planted errors and their status, without their content", _list)
    lister.add_argument("--db", default="oversight.db", help="to see voids in effect")
    lister.add_argument("--queue")
    lister.add_argument("--status", choices=[s.value for s in Status])

    draft = action(
        "draft",
        "draft a file from a correct item, for you to review and edit",
        _draft,
        database=False,
    )
    draft.add_argument("--queue", required=True)
    draft.add_argument(
        "--from",
        dest="source",
        required=True,
        metavar="FILE",
        help="a correct item (synthetic or de-identified), or a JSON list of them",
    )
    draft.add_argument(
        "--item", type=int, default=0, help="which item of a list, from 0 (default: 0)"
    )
    draft.add_argument(
        "--mutator",
        required=True,
        choices=("value-swap", "omission", "classification-flip"),
    )
    draft.add_argument("--field", required=True, help="the property to change")
    draft.add_argument("--new-value", help="value-swap: the wrong value (JSON unless text)")
    draft.add_argument("--remove-index", type=int, help="omission: which list entry, from 0")
    draft.add_argument("--to", help="classification-flip: the wrong class")
    draft.add_argument("--id", required=True, dest="planted_error_id")
    draft.add_argument("--author", required=True)
    draft.add_argument("--out", required=True, metavar="FILE", help="never overwritten")


def _guarded(run: Callable) -> Callable:
    def wrapper(args: argparse.Namespace) -> int:
        try:
            config = load_config(args.config)
            return run(args, config)
        except _ERRORS as error:
            print(f"error: {error}", file=sys.stderr)
            return common.EXIT_ERROR

    return wrapper


def _library(args: argparse.Namespace, config: Config):
    return closing(open_planted_db(args.planted))


def _describe(library: PlantedLibrary, config: Config, planted: PlantedError) -> str:
    queue = config.queues.get(planted.queue)
    needed = approvals_needed(True if queue is None else queue.gxp)
    status = library.status(planted).value.replace("_", " ")
    pool = "" if planted.purpose.value == "live" else f"  [{planted.purpose.value}]"
    return (
        f"{planted.planted_error_id}  {planted.queue}  {planted.defect_class.value}{pool}  "
        f"{status}  ({len(planted.approvers)} of {needed} approvals)"
    )


def _import(args: argparse.Namespace, config: Config) -> int:
    failed = 0
    with _library(args, config) as connection:
        library = PlantedLibrary(connection, config)
        for path in args.files:
            try:
                planted = library.import_file(path)
            except PlantedImportError as error:
                print(f"refused: {error}", file=sys.stderr)
                failed += 1
                continue
            print(f"imported {_describe(library, config, planted)}")
    return common.EXIT_ERROR if failed else common.EXIT_OK


def _approve(args: argparse.Namespace, config: Config) -> int:
    with _library(args, config) as connection:
        library = PlantedLibrary(connection, config)
        planted = library.approve(args.planted_error_id, args.approver)
        print(_describe(library, config, planted))
    return common.EXIT_OK


def _retire(args: argparse.Namespace, config: Config) -> int:
    with _library(args, config) as connection:
        library = PlantedLibrary(connection, config)
        planted = library.retire(args.planted_error_id, args.by, args.reason)
        print(_describe(library, config, planted))
    return common.EXIT_OK


def _with_voids(args: argparse.Namespace, config: Config, step: Callable) -> int:
    """Open the planted error's queue, which writing a void needs, and take one step."""
    with _library(args, config) as connection:
        queue = PlantedLibrary(connection, config).get(args.planted_error_id).queue
    with Oversight(config, queue, args.db, args.planted) as oversight:
        step(oversight.voids())
    return common.EXIT_OK


def _void(args: argparse.Namespace, config: Config) -> int:
    def step(voids) -> None:
        voids.request(args.planted_error_id, args.by, args.reason, args.explanation, args.reference)
        print(
            f"{args.planted_error_id}: void requested. Nothing has changed. A different person "
            "must run rp plant void-confirm."
        )

    return _with_voids(args, config, step)


def _void_confirm(args: argparse.Namespace, config: Config) -> int:
    def step(voids) -> None:
        voids.confirm(args.planted_error_id, args.by)
        print(f"{args.planted_error_id}: voided. All its results are now left out of scores.")
        print(
            "Reports show what was left out. Check rp qualify status for anyone whose session "
            "held it, and list who met it with planted_records.py in the validation pack."
        )

    return _with_voids(args, config, step)


def _void_cancel(args: argparse.Namespace, config: Config) -> int:
    def step(voids) -> None:
        voids.cancel(args.planted_error_id, args.by)
        print(f"{args.planted_error_id}: the request to void it is cancelled.")

    return _with_voids(args, config, step)


def _void_marks(args: argparse.Namespace, connection) -> dict[str, str]:
    """What to show beside each planted error that has a void or a request for one."""
    pending = [void for void in read_voids(connection) if void.cancelled_by is None]
    if not pending:
        return {}
    try:
        with closing(open_readonly(args.db, "oversight")) as oversight:
            in_effect = voids_in_effect(oversight, connection)
    except StoreError as error:
        # Never guess: without the log, no void is known to be in effect.
        print(f"note: cannot tell which voids are in effect: {error}", file=sys.stderr)
        return {void.planted_error_id: "void recorded: not checked" for void in pending}
    words = {
        VoidState.IN_EFFECT: "VOIDED",
        VoidState.AWAITING_CONFIRMATION: "void awaiting confirmation",
        VoidState.NOT_COMPLETED: "void not completed: the confirmer must confirm again",
    }
    return {void.planted_error_id: words[state_of(void, in_effect)] for void in pending}


def _list(args: argparse.Namespace, config: Config) -> int:
    with _library(args, config) as connection:
        library = PlantedLibrary(connection, config)
        marks = _void_marks(args, connection)
        shown = 0
        for planted in library.all(args.queue):
            if args.status and library.status(planted).value != args.status:
                continue
            mark = marks.get(planted.planted_error_id)
            print(_describe(library, config, planted) + (f"  [{mark}]" if mark else ""))
            if library.status(planted) is Status.APPROVED:
                for problem in library.problems(planted):
                    print(f"    held back: {problem}")
            shown += 1
        print(f"{shown} planted error(s).")
    return common.EXIT_OK


def _value(text: str, field: str, source: dict) -> Any:
    """Text for a text field; otherwise JSON, such as ``true`` or ``12.5``."""
    if isinstance(source.get(field), str):
        return text
    try:
        return json.loads(text)
    except ValueError:
        return text


def _draft(args: argparse.Namespace, config: Config) -> int:
    queue = config.queues.get(args.queue)
    if queue is None:
        raise mutators.DraftError(f"queue {args.queue!r} is not in the settings")
    source = load_file(Path(args.source))
    if isinstance(source, list):
        if not 0 <= args.item < len(source):
            raise mutators.DraftError(f"--item must be from 0 to {len(source) - 1}")
        source = source[args.item]
    if not isinstance(source, dict):
        raise mutators.DraftError("--from must hold an item, or a list of items")

    common_arguments = {
        "planted_error_id": args.planted_error_id,
        "author": args.author,
        "field": args.field,
    }
    if args.mutator == "value-swap":
        if args.new_value is None:
            raise mutators.DraftError("value-swap needs --new-value")
        draft = mutators.draft_value_swap(
            queue,
            source,
            new_value=_value(args.new_value, args.field, source),
            **common_arguments,
        )
    elif args.mutator == "omission":
        draft = mutators.draft_omission(queue, source, index=args.remove_index, **common_arguments)
    else:
        to = None if args.to is None else _value(args.to, args.field, source)
        draft = mutators.draft_classification_flip(queue, source, to=to, **common_arguments)
    mutators.write_draft(draft, args.out)
    print(f"Draft written to {args.out}. Work through its checklist before importing it.")
    return common.EXIT_OK
