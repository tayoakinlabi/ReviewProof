"""What the API and the guard share: finding items, and writing their events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from reviewproof.canonical import canonical_json, sha256_hex
from reviewproof.errors import OversightError, UnknownTokenError
from reviewproof.models import Decision, EventType
from reviewproof.store.eventlog import EventLog


@dataclass(frozen=True)
class ItemRef:
    item_id: str
    #: The oversight context the item came from, which may be older than the current one.
    system_id: str


class Records:
    """One queue's items. Call the methods inside ``log.transaction()``."""

    def __init__(self, db: sqlite3.Connection, log: EventLog, queue: str) -> None:
        self.db = db
        self.log = log
        self.queue = queue

    def find(self, token: str) -> ItemRef:
        row = None
        if isinstance(token, str):
            row = self.db.execute(
                "SELECT item_id, system_id FROM items WHERE token_hash = ? AND queue_id = ?",
                (token_hash(token), self.queue),
            ).fetchone()
        if row is None:
            # The token is not repeated: it is a secret and could end up in the host's logs.
            raise UnknownTokenError("no item in this queue has that token")
        return ItemRef(row[0], row[1])

    def reviewers_of(self, item_id: str, event_type: EventType) -> list[str | None]:
        """The reviewer of each event of one type on an item."""
        rows = self.db.execute(
            "SELECT reviewer_id FROM events WHERE item_id = ? AND type = ? ORDER BY event_id",
            (item_id, event_type.value),
        )
        return [row[0] for row in rows]

    def append(
        self,
        event_type: EventType,
        item: ItemRef,
        reviewer: str | None,
        decision: Decision | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        self.log.append(
            event_type,
            system_id=item.system_id,
            queue_id=self.queue,
            item_id=item.item_id,
            reviewer_id=reviewer,
            decision=decision,
            data=data,
        )


def token_hash(token: str) -> str:
    return sha256_hex(token.encode("utf-8", errors="surrogatepass"))


def require_reviewer(reviewer: Any) -> None:
    if not isinstance(reviewer, str) or not reviewer or reviewer != reviewer.strip():
        raise OversightError("reviewer must be a non-empty ID with no surrounding spaces")


MAX_KEY_LENGTH = 500


def require_key(key: Any) -> None:
    if (
        not isinstance(key, str)
        or not key
        or key != key.strip()
        or len(key) > MAX_KEY_LENGTH
        or any("\ud800" <= character <= "\udfff" for character in key)
    ):
        raise OversightError(
            f"key must be text of 1 to {MAX_KEY_LENGTH} characters with no surrounding spaces"
        )


def plain_json(value: Any, what: str) -> dict[str, Any]:
    """A detached copy made of plain JSON types, however the caller built it."""
    if not isinstance(value, Mapping):
        raise OversightError(f"{what} must be a mapping")
    try:
        return json.loads(canonical_json(dict(value)))
    except (TypeError, ValueError, RecursionError) as error:
        raise OversightError(f"{what} may hold only JSON values: {error}") from error
