"""Entry point for the ``rp`` command."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from reviewproof import __version__
from reviewproof.cli import craft, plant, qualify, report, setup, verify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rp",
        description="Test whether human reviewers of AI output catch errors.",
    )
    parser.add_argument("--version", action="version", version=f"rp {__version__}")
    subparsers = parser.add_subparsers(title="commands", metavar="COMMAND")
    setup.add_parsers(subparsers)
    plant.add_parser(subparsers)
    craft.add_parser(subparsers)
    qualify.add_parser(subparsers)
    report.add_parsers(subparsers)
    verify.add_parser(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "run"):
        parser.print_help()
        return 0
    return args.run(args)
