"""Shared by the ``rp`` commands."""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta, timezone

#: Exit statuses, the same for every command.
EXIT_OK = 0
EXIT_FAILED = 1  # the evidence did not verify
EXIT_ERROR = 2  # the command could not be run
EXIT_NOT_DELIVERED = 3  # an alert could not be sent

EXIT_HELP = "Exit status: 0 done, 1 the evidence did not verify, 2 could not run."

_RELATIVE = re.compile(r"(\d+)([hdw])")
_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


def add_settings(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config", default="reviewproof.yaml", help="settings file (default: %(default)s)"
    )


def add_databases(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="oversight.db", help="default: %(default)s")
    parser.add_argument("--planted", default="planted.db", help="default: %(default)s")


def since(text: str, now: datetime | None = None) -> datetime:
    """``30d``, ``12h``, ``4w``, or a date or time such as ``2026-11-01`` (UTC)."""
    now = now or datetime.now(timezone.utc)
    match = _RELATIVE.fullmatch(text)
    try:
        if match:
            return now - timedelta(**{_UNITS[match[2]]: int(match[1])})
        moment = datetime.fromisoformat(text)
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a period: use 30d, 12h, 4w or a date like 2026-11-01"
        ) from None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
