"""``rp report`` and ``rp alerts``."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from reviewproof import alerts
from reviewproof.cli import common
from reviewproof.cli.verify import read_head
from reviewproof.config import Config, ConfigError, load_config
from reviewproof.metrics.checks import Check, State
from reviewproof.metrics.projection import EvidenceError, load
from reviewproof.report import Report, ReportError, build, html, tables
from reviewproof.store.schema import StoreError

_ERRORS = (ConfigError, StoreError, ReportError, ValueError, OSError)


def _shared(parser: argparse.ArgumentParser) -> None:
    common.add_settings(parser)
    common.add_databases(parser)
    parser.add_argument(
        "--queue", action="append", help="repeat for several (default: every queue in the log)"
    )
    parser.add_argument(
        "--since", type=common.since, help="only decisions since then: 30d, 12h, 4w or a date"
    )
    parser.add_argument(
        "--expect-head", metavar="FILE", help="also check against a head exported earlier"
    )


def add_parsers(subparsers: argparse._SubParsersAction) -> None:
    report = subparsers.add_parser(
        "report",
        help="make the report for QA",
        description="Verify the evidence, then report how review performed. No scores are "
        "shown if the evidence does not verify.",
        epilog=common.EXIT_HELP,
    )
    _shared(report)
    report.add_argument("--format", choices=("html", "csv"), default="html")
    report.add_argument("--out", metavar="FILE", help="default: print to standard output")
    report.add_argument(
        "--individuals",
        action="store_true",
        help="include results for each reviewer; for QA only, under a written policy",
    )
    report.set_defaults(run=run_report)

    alert = subparsers.add_parser(
        "alerts",
        help="check the thresholds and alert the QA lead",
        description="Check each queue against its thresholds. Without --send, only print. "
        "Run it on a schedule with --send.",
        epilog="Exit status: 0 done, 1 the evidence did not verify, 2 could not run, "
        "3 an alert could not be sent.",
    )
    _shared(alert)
    alert.add_argument(
        "--send", action="store_true", help="send to the route in the settings (alerts.qa_lead)"
    )
    alert.set_defaults(run=run_alerts)


def _make(args: argparse.Namespace, config: Config, individuals: bool) -> Report:
    expected = read_head(Path(args.expect_head)) if args.expect_head else None
    projection = load(args.db, args.planted, expected)
    return build(
        projection,
        config,
        args.planted,
        queues=args.queue,
        since=args.since,
        individuals=individuals,
        anchored=expected is not None,
    )


def _write(text: str, destination: str | None) -> None:
    if destination is None:
        # Bytes, so that a Windows console code page cannot mangle or reject the text.
        sys.stdout.flush()
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            sys.stdout.write(text)
        else:
            buffer.write(text.encode("utf-8"))
            buffer.flush()
        return
    with open(destination, "w", encoding="utf-8", newline="\n") as file:
        file.write(text)


def run_report(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        try:
            report = _make(args, config, args.individuals)
        except EvidenceError as error:
            # A page that says so, not an old report left looking current.
            if args.format == "html":
                _write(html.render_failure(error.result), args.out)
            print(f"FAILED: {error}", file=sys.stderr)
            return common.EXIT_FAILED
        render = html.render if args.format == "html" else tables.render
        _write(render(report), args.out)
    except _ERRORS as error:
        print(f"error: {error}", file=sys.stderr)
        return common.EXIT_ERROR
    if args.out:
        print(f"Report written to {args.out}", file=sys.stderr)
    return common.EXIT_OK


def run_alerts(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        try:
            report = _make(args, config, individuals=False)
        except EvidenceError:
            failed = Check(
                "evidence",
                "Evidence check",
                State.BREACH,
                "The review log or item records did not verify. No scores can be trusted. "
                "Run rp verify.",
            )
            message = alerts.compose("(all queues)", [failed], datetime.now(timezone.utc))
            print(message.body)
            if args.send and not _deliver(message, config):
                return common.EXIT_NOT_DELIVERED
            return common.EXIT_FAILED
    except _ERRORS as error:
        print(f"error: {error}", file=sys.stderr)
        return common.EXIT_ERROR

    undelivered = False
    for queue in report.queues:
        breaches = [c for c in queue.checks if c.state is State.BREACH]
        unjudged = sum(c.state is State.NOT_ENOUGH_DATA for c in queue.checks)
        if not breaches:
            note = f" ({unjudged} measure(s) cannot be judged yet)" if unjudged else ""
            print(f"{queue.name}: nothing outside its limit{note}.")
            continue
        message = alerts.compose(queue.name, breaches, report.generated_at)
        print(f"{message.subject}\n{message.body}\n")
        if args.send:
            undelivered = not _deliver(message, config) or undelivered
    return common.EXIT_NOT_DELIVERED if undelivered else common.EXIT_OK


def _deliver(message: alerts.Message, config: Config) -> bool:
    try:
        delivered = alerts.send(message, config.alerts)
    except alerts.DeliveryError as error:
        print(f"error: the alert was not delivered: {error}", file=sys.stderr)
        return False
    print(f"Sent by {', '.join(delivered)}.", file=sys.stderr)
    return True
