"""Build the golden log from ``golden/scenario.csv`` through the public API.

The clock and the gate's random source are scripted, so the history is exactly the one in the
scenario while every row is written by the real code.
"""

import copy
import csv
import dataclasses
import shutil
from datetime import datetime, timezone
from pathlib import Path

from deploy import DEMO, ITEMS, QUEUE

from reviewproof import Item, Oversight, initialise
from reviewproof.canonical import format_timestamp
from reviewproof.config import load_config
from reviewproof.records import token_hash
from reviewproof.store import open_oversight_db

GOLDEN = Path(__file__).parent / "golden"

DEFECTS = {
    "omission": "immediate_actions",
    "wrong_classification": "classification",
    "wrong_value": "lot_number",
    "policy_breach": "capa_required",
}
CLASS_OF = dict(
    zip(
        (f"PE-{n:02d}" for n in range(1, 13)),
        [name for name in DEFECTS for _ in range(3)],
        strict=True,
    )
)


def planted_of(label):
    """P05, Q01 and X01 are all planted items; the number names the planted error."""
    return f"PE-{label[1:]}" if label[0] in "PQX" else None


def read(name):
    with (GOLDEN / name).open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


class Clock:
    def __init__(self):
        self.now = "2026-03-02T08:00:00.000000Z"

    def set(self, text):
        moment = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        self.now = format_timestamp(moment)

    def __call__(self):
        return self.now


class ScriptedGate:
    """Stands in for the random source: releases exactly the planted error that is named."""

    wanted = None

    def random(self):
        return 0.0 if self.wanted else 0.999

    def choice(self, candidates):
        matching = [p for p in candidates if p.planted_error_id == self.wanted]
        assert matching, f"{self.wanted} is not among the gate's candidates"
        return matching[0]

    def shuffle(self, entries):
        pass


def planted_input(planted_error_id, number):
    defect_class = CLASS_OF[planted_error_id]
    field = DEFECTS[defect_class]
    base = next(i for i in ITEMS if i["classification"] == "critical")
    payload = copy.deepcopy(base)
    payload["deviation_id"] = f"DEV-2025-{900 + number:04d}"
    if field == "immediate_actions":
        payload[field] = payload[field][:2]
    elif field == "classification":
        payload[field] = "major"
    elif field == "lot_number":
        payload[field] = f"LOT-Z{number:05d}"
    else:
        payload[field] = False
    return {
        "planted_error_id": planted_error_id,
        "queue": QUEUE,
        "defect_class": defect_class,
        "defect_field": field,
        "expected_decision": "reject",
        "author": "author-1",
        "payload": payload,
    }


def edited(payload, field):
    changed = copy.deepcopy(payload)
    if field == "classification":
        changed[field] = "minor"
    elif field == "capa_required":
        changed[field] = not changed[field]
    elif field == "lot_number":
        changed[field] = "LOT-Y00001"
    else:
        changed[field] += " (corrected)"
    return changed


@dataclasses.dataclass
class Golden:
    config: object
    db: Path
    planted_db: Path
    #: scenario label -> item ID
    ids: dict

    def label_of(self, item_id):
        return next(label for label, found in self.ids.items() if found == item_id)


def build(folder: Path) -> Golden:
    for name in ("reviewproof.yaml", "item.schema.json"):
        shutil.copy(DEMO / name, folder / name)
    config = load_config(folder / "reviewproof.yaml")
    queue = dataclasses.replace(config.queues[QUEUE], planted_rate_override=0.2)
    config = dataclasses.replace(config, queues={QUEUE: queue})
    db, planted_db = folder / "oversight.db", folder / "planted.db"
    initialise(db, planted_db)

    rows = read("scenario.csv")
    by_label = {row["label"]: row for row in rows}
    attached = {row["released_with"]: row["label"] for row in rows if row["released_with"]}
    clock, gate, tickets = Clock(), ScriptedGate(), {}

    with Oversight(config, QUEUE, db, planted_db, rng=gate, clock=clock) as ov:
        for number in range(1, 13):
            planted_error_id = f"PE-{number:02d}"
            ov.planted.import_data(planted_input(planted_error_id, number))
            ov.planted.approve(planted_error_id, "qa-1")
            ov.planted.approve(planted_error_id, "qa-2")

        def submit(label):
            row = by_label[label]
            number = int(label[1:])
            payload = {**ITEMS[number % 20], "deviation_id": f"DEV-2025-{number:04d}"}
            partner = attached.get(label)
            gate.wanted = planted_of(partner) if partner else None
            result = ov.submit(Item(payload=payload, target_id=f"QMS-{label}"), row["hint"] or None)
            assert len(result) == (2 if partner else 1)
            tickets[label] = result[0]  # shuffling is off, so the real ticket is first
            if partner:
                tickets[partner] = result[1]

        def review(labels):
            """Showings and decisions, in time order."""
            actions = []
            for label in labels:
                row = by_label[label]
                for extra in filter(None, row["also_shown"].split(";")):
                    who, at = extra.split("@")
                    actions.append((at, 0, label, who))
                if row["shown_at"]:
                    actions.append((row["shown_at"], 0, label, row["reviewer"]))
                if row["decided_at"]:
                    actions.append((row["decided_at"], 1, label, row["reviewer"]))
            for at, is_decision, label, who in sorted(actions):
                clock.set(at)
                token = tickets[label].token
                if not is_decision:
                    ov.shown(token, who)
                    continue
                row = by_label[label]
                final = None
                if row["decision"] == "edit":
                    final = edited(tickets[label].payload, row["edited_field"])
                ov.decided(token, who, row["decision"], final_payload=final)

        def with_partners(first, last):
            reals = [f"R{n}" for n in range(first, last + 1)]
            return reals + [attached[r] for r in reals if r in attached]

        # A's items are all submitted first, then reviewed.
        clock.set("2026-03-02T08:00:00")
        for n in range(1, 13):
            submit(f"R{n}")
        review(with_partners(1, 12))
        clock.set("2026-03-09T13:00:00")
        for n in range(13, 17):
            submit(f"R{n}")
        review(with_partners(13, 16))
        # Leave B only planted errors they have met, so that a repeat can be scripted.
        for number in (3, 5, 6, 8, 9, 10, 11, 12):
            ov.planted.retire(f"PE-{number:02d}", "qa-1")
        clock.set("2026-03-09T14:30:00")
        submit("R17")
        review(with_partners(17, 17))
        clock.set("2026-03-09T15:05:00")
        submit("R18")
        review(with_partners(18, 18))
        submit("R19")

    with_ids = {}
    connection = open_oversight_db(db)
    try:
        for label, ticket in tickets.items():
            with_ids[label] = connection.execute(
                "SELECT item_id FROM items WHERE token_hash = ?", (token_hash(ticket.token),)
            ).fetchone()[0]
    finally:
        connection.close()
    return Golden(config, db, planted_db, with_ids)
