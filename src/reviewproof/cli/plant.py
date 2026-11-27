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
from reviewproof.cli import common
from reviewproof.config import Config, ConfigError, load_config
from reviewproof.itemschema import SchemaError
from reviewproof.planted import (
    ApprovalError,
    PlantedError,
    PlantedImportError,
    PlantedLibrary,
    Status,
    UnknownPlantedError,
    approvals_needed,
)
from reviewproof.store.schema import StoreError, open_planted_db
from reviewproof.strictload import LoadError, load_file

_ERRORS = (
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

    lister = action("list", "list planted errors and their status, without their content", _list)
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
    return (
        f"{planted.planted_error_id}  {planted.queue}  {planted.defect_class.value}  "
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


def _list(args: argparse.Namespace, config: Config) -> int:
    with _library(args, config) as connection:
        library = PlantedLibrary(connection, config)
        shown = 0
        for planted in library.all(args.queue):
            if args.status and library.status(planted).value != args.status:
                continue
            print(_describe(library, config, planted))
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
