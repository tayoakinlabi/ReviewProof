"""Canonical JSON, hashing and timestamps (ADR-002).

Hashes made from these functions are evidence. Changing their output breaks every existing log.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def canonical_json(value: Any) -> bytes:
    """Return the one UTF-8 encoding of ``value`` that gets hashed.

    Rejects anything that would not survive a round trip: NaN, infinity, non-string object keys,
    tuples and other non-JSON types.
    """
    _check_json(value, "value")
    text = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return text.encode("utf-8")


def _check_json(value: Any, where: str) -> None:
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, list):
        for index, element in enumerate(value):
            _check_json(element, f"{where}[{index}]")
        return
    if isinstance(value, dict):
        for key, element in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{where} has a non-string key: {key!r}")
            _check_json(element, f"{where}.{key}")
        return
    raise TypeError(f"{where} is not a JSON value: {type(value).__name__}")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def format_timestamp(moment: datetime) -> str:
    """Format an aware datetime as ``YYYY-MM-DDTHH:MM:SS.ffffffZ``."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utc_now() -> str:
    return format_timestamp(datetime.now(timezone.utc))
