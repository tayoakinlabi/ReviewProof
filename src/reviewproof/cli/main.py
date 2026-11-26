"""Entry point for the ``rp`` command."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from reviewproof import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rp",
        description="Test whether human reviewers of AI output catch errors.",
    )
    parser.add_argument("--version", action="version", version=f"rp {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
