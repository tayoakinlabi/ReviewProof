"""``rp qualify``: run qualification sessions from files, away from the live queue."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from reviewproof.api import Oversight
from reviewproof.cli import common
from reviewproof.config import ConfigError, load_config
from reviewproof.errors import OversightError, TransitionError
from reviewproof.metrics.checks import CLASS_NAMES
from reviewproof.qualification import Result, load_plan
from reviewproof.store.schema import StoreError
from reviewproof.strictload import LoadError, load_file

_ERRORS = (ConfigError, StoreError, OversightError, LoadError, OSError, ValueError)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    qualify = subparsers.add_parser(
        "qualify",
        help="test a reviewer with a qualification session",
        description="Qualification sessions: items with known answers, scored against a pass "
        "mark fixed before the session starts. For QA administrators.",
    )
    actions = qualify.add_subparsers(title="actions", metavar="ACTION", required=True)

    def action(name: str, help_text: str, run: Callable):
        parser = actions.add_parser(
            name,
            help=help_text,
            description=help_text.capitalize() + ".",
            epilog="Exit status: 0 done, 2 refused or could not run.",
        )
        common.add_settings(parser)
        common.add_databases(parser)
        parser.add_argument("--queue", required=True)
        parser.set_defaults(run=_guarded(run))
        return parser

    start = action("start", "start a session and write the reviewer's worksheet", _start)
    start.add_argument("--reviewer", required=True)
    start.add_argument("--plan", required=True, metavar="FILE", help="the session plan")
    start.add_argument(
        "--out", required=True, metavar="FILE", help="the worksheet; never overwritten"
    )

    finish = action("finish", "record the answers and score the session", _finish)
    finish.add_argument("--answers", required=True, metavar="FILE", help="YAML or JSON")

    abandon = action("abandon", "end a session early; it counts as not passed", _abandon)
    abandon.add_argument("session_id", metavar="SESSION")

    status = action("status", "show whether a reviewer is qualified", _status)
    status.add_argument("--reviewer", required=True)


def _guarded(run: Callable) -> Callable:
    def wrapper(args: argparse.Namespace) -> int:
        try:
            config = load_config(args.config)
            with Oversight(config, args.queue, args.db, args.planted) as oversight:
                return run(args, oversight.qualification())
        except _ERRORS as error:
            print(f"error: {error}", file=sys.stderr)
            return common.EXIT_ERROR

    return wrapper


def _start(args: argparse.Namespace, qualification) -> int:
    plan = load_plan(args.plan)
    # Opened first, so that a session is never started without somewhere to put it.
    with Path(args.out).open("x", encoding="utf-8", newline="\n") as file:
        try:
            session = qualification.start(args.reviewer, plan)
        except BaseException:
            file.close()
            Path(args.out).unlink()
            raise
        json.dump(
            {
                "session_id": session.session_id,
                "reviewer": session.reviewer,
                "queue": args.queue,
                "items": [{"token": item.token, "payload": item.payload} for item in session.items],
            },
            file,
            indent=2,
            ensure_ascii=False,
        )
    print(f"Session {session.session_id} started with {len(session.items)} items.")
    print(f"Worksheet: {args.out}")
    return common.EXIT_OK


def _finish(args: argparse.Namespace, qualification) -> int:
    data = load_file(Path(args.answers))
    if (
        not isinstance(data, dict)
        or set(data) != {"session_id", "reviewer", "answers"}
        or not isinstance(data["answers"], list)
    ):
        raise LoadError("an answers file holds session_id, reviewer and a list of answers")
    for number, answer in enumerate(data["answers"]):
        allowed = {"token", "decision", "final_payload"}
        if not isinstance(answer, dict) or not {"token", "decision"} <= set(answer) <= allowed:
            raise LoadError(f"answers[{number}] needs token and decision")
        try:
            qualification.answered(
                answer["token"],
                data["reviewer"],
                answer["decision"],
                answer.get("final_payload"),
            )
        except TransitionError as error:
            # So that a corrected file can be run again.
            if "already been answered" not in str(error):
                raise
            print(f"answers[{number}] was already recorded; it was not changed.")
    _print_result(qualification.finish(data["session_id"]))
    return common.EXIT_OK


def _abandon(args: argparse.Namespace, qualification) -> int:
    _print_result(qualification.abandon(args.session_id))
    return common.EXIT_OK


def _status(args: argparse.Namespace, qualification) -> int:
    state = qualification.status(args.reviewer)
    print(f"{args.reviewer}: {state.status.value.replace('_', ' ')}")
    if state.session_id:
        print(f"  passed session {state.session_id} on {state.passed_at[:10]}")
    return common.EXIT_OK


def _print_result(result: Result) -> None:
    outcome = "ABANDONED" if result.abandoned else "PASSED" if result.passed else "NOT PASSED"
    print(f"Session {result.session_id}, reviewer {result.reviewer}: {outcome}")
    print(
        f"  Defects caught: {result.caught} of {result.defects} "
        f"(pass mark: at least {result.criteria.min_caught})"
    )
    print(
        f"  Correct items wrongly rejected or changed: {result.false_rejects} of "
        f"{result.correct_items} (pass mark: at most {result.criteria.max_false_rejects})"
    )
    for defect, (caught, total) in sorted(result.by_class.items()):
        print(f"    {CLASS_NAMES[defect.value]}: {caught} of {total}")
    print("Confidential: this is one person's result.")
