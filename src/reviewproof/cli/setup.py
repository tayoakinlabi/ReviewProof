"""``rp init`` and ``rp system``."""

from __future__ import annotations

import argparse
import sys

from reviewproof.api import Oversight, initialise
from reviewproof.cli import common
from reviewproof.config import ConfigError, load_config
from reviewproof.errors import OversightError
from reviewproof.store.schema import StoreError, needs_upgrade

_ERRORS = (ConfigError, StoreError, OversightError, OSError, ValueError)


def add_parsers(subparsers: argparse._SubParsersAction) -> None:
    init = subparsers.add_parser(
        "init",
        help="create the two databases, or upgrade them",
        description="Create the databases when setting up, or upgrade ones made by an "
        "earlier release. Back up both before an upgrade: it cannot be undone. Refuses to "
        "replace one that has been lost: restore both from the same backup.",
        epilog="Exit status: 0 done, 2 could not run.",
    )
    common.add_databases(init)
    init.set_defaults(run=run_init)

    system = subparsers.add_parser(
        "system",
        help="record a change to the AI system",
        description="Manage the oversight context: the version of the AI system under review.",
    )
    actions = system.add_subparsers(title="actions", metavar="ACTION", required=True)
    register = actions.add_parser(
        "register",
        help="record a new model, prompt or tool version",
        description="Record a new version for a queue's AI system and flag its reviewers for "
        "requalification. Update the settings file too: the next time ReviewProof opens, the "
        "file wins.",
        epilog="Exit status: 0 done, 2 could not run.",
    )
    common.add_settings(register)
    common.add_databases(register)
    register.add_argument("--queue", required=True)
    for name in ("model-id", "model-version", "prompt-version", "tool-schema-hash"):
        register.add_argument(f"--{name}")
    register.set_defaults(run=run_register)


def run_init(args: argparse.Namespace) -> int:
    try:
        upgraded = [
            path
            for path, component in ((args.db, "oversight"), (args.planted, "planted"))
            if needs_upgrade(path, component)
        ]
        initialise(args.db, args.planted, upgrade=True)
    except _ERRORS as error:
        print(f"error: {error}", file=sys.stderr)
        return common.EXIT_ERROR
    for path in upgraded:
        print(f"Upgraded {path}. Earlier releases can no longer open it.")
    print(f"Ready: {args.db} and {args.planted}.")
    print(f"Give write access to {args.planted} only to the application and QA administrators.")
    return common.EXIT_OK


def run_register(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        with Oversight(config, args.queue, args.db, args.planted) as oversight:
            flagged = oversight.register_system(
                model_id=args.model_id,
                model_version=args.model_version,
                prompt_version=args.prompt_version,
                tool_schema_hash=args.tool_schema_hash,
            )
    except _ERRORS as error:
        print(f"error: {error}", file=sys.stderr)
        return common.EXIT_ERROR
    print(f"Recorded. {len(flagged)} reviewer(s) flagged for requalification.")
    for reviewer in flagged:
        print(f"  {reviewer}")
    print(f"Now update {args.config} to match, or the old version will be recorded again.")
    return common.EXIT_OK
