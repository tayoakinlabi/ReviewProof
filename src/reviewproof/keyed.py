"""Submissions with a key: safe to repeat, and traceable from a paused call to its decision
(ADR-005).

A key names one proposed action. For a keyed submission each ticket's token is derived from a
random secret and the item ID, so a repeat can hand out the same tickets although only token
hashes are in the log. The secret and the key are in ``planted.db`` only. A key is not written
to the log, where it would group the two items of a pair.

The key rows say where to look and are not trusted: everything found through them is checked
against the log.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from reviewproof.errors import OversightError
from reviewproof.itemschema import ItemSchema
from reviewproof.models import (
    CallState,
    CallStatus,
    EventType,
    ItemKind,
    Ticket,
    derive_token,
)
from reviewproof.planted import PlantedLibrary
from reviewproof.records import Records, plain_json, token_hash
from reviewproof.store.commitments import payload_hash
from reviewproof.store.eventlog import item_events
from reviewproof.store.registry import RegistryEntry, commitment_of, key_rows, read_entry
from reviewproof.store.schema import StoreError


@dataclass(frozen=True)
class _Keyed:
    """One item of a keyed submission, checked against the log."""

    token: str = field(repr=False)
    entry: RegistryEntry = field(repr=False)
    reviewer: str | None = field(repr=False)
    events: list[dict[str, Any]] = field(repr=False)

    @property
    def submitted(self) -> dict[str, Any]:
        return self.events[0]["data"]


class KeyedSubmissions:
    """One queue's keyed submissions. Call the methods inside ``log.transaction()``."""

    def __init__(
        self, records: Records, planted: sqlite3.Connection, library: PlantedLibrary
    ) -> None:
        self._records = records
        self._planted = planted
        self._library = library

    def replay(
        self,
        key: str,
        target_id: str,
        payload: dict[str, Any],
        reviewer: str | None,
        schema: ItemSchema | None,
    ) -> list[Ticket] | None:
        """The tickets already issued under ``key``, or None if the key is new.

        Writes nothing. A key used for anything else is refused.
        """
        items = self._items(key)
        if not items:
            return None
        real = self._real(items)
        same = (
            real.entry.target_id == target_id
            and real.reviewer == reviewer
            and payload_hash(payload, real.entry.payload_salt) == real.submitted["payload_hash"]
        )
        if not same:
            raise OversightError(
                "this key was already used for a different target, payload or reviewer. A key "
                "names one proposed action. If the proposal is built in code that can run "
                "again, build it first and keep it."
            )
        if schema is None and len(items) > 1:
            raise OversightError(
                "the item schema cannot be used, so the earlier tickets cannot be shown alike"
            )
        tickets = []
        for item in items:
            content = payload if item is real else self._planted_payload(item)
            tickets.append(Ticket(item.token, schema.arrange(content) if schema else content))
        return tickets

    def status(self, key: str) -> CallStatus:
        """What has been decided about the real item under ``key``."""
        real = self._real(self._known(key))
        decisions = [e for e in real.events if e["type"] == EventType.DECIDED.value]
        if not decisions:
            return CallStatus(key, CallState.WAITING)
        if len(decisions) != 1:
            raise StoreError("the log's account of this item is inconsistent; run rp verify")
        try:
            return CallStatus(key, CallState(decisions[0]["decision"]))
        except ValueError as error:
            raise StoreError("the log holds an unknown decision; run rp verify") from error

    def real_token(self, key: str) -> str:
        """The real item's token. The guard still makes every one of its checks."""
        return self._real(self._known(key)).token

    # ------------------------------------------------------------- finding

    def _known(self, key: str) -> list[_Keyed]:
        items = self._items(key)
        if not items:
            raise OversightError("nothing in this queue was submitted with that key")
        return items

    def _items(self, key: str) -> list[_Keyed]:
        """The items submitted under ``key``, in the order their tickets were returned.

        A key row whose item never reached the log was left by a submission that failed.
        """
        found = []
        for item_id, seed, reviewer in key_rows(self._planted, self._records.queue, key):
            row = self._records.db.execute(
                "SELECT token_hash FROM items WHERE item_id = ? AND queue_id = ?",
                (item_id, self._records.queue),
            ).fetchone()
            if row is None:
                continue
            token = derive_token(seed, item_id)
            events = item_events(self._records.db, item_id)
            entry = read_entry(self._planted, item_id)
            if (
                row[0] != token_hash(token)
                or not events
                or events[0]["type"] != EventType.SUBMITTED.value
                or events[0]["data"].get("commitment") != commitment_of(self._planted, entry)
            ):
                raise StoreError("the records for this key do not match the log; run rp verify")
            found.append(_Keyed(token, entry, reviewer, events))
        return found

    @staticmethod
    def _real(items: list[_Keyed]) -> _Keyed:
        real = [item for item in items if item.entry.kind is ItemKind.REAL]
        if len(real) != 1:
            raise StoreError("the records for this key are inconsistent; run rp verify")
        return real[0]

    def _planted_payload(self, item: _Keyed) -> dict[str, Any]:
        planted = self._library.get(item.entry.planted_error_id)
        content = plain_json(planted.payload, "payload")
        if payload_hash(content, item.entry.payload_salt) != item.submitted["payload_hash"]:
            raise StoreError("the records for this key do not match the log; run rp verify")
        return content
