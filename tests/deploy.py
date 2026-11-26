"""A throwaway deployment of the demo queue, shared by the API, guard and Gate 1 tests."""

import copy
import dataclasses
import json
import random
import shutil
from contextlib import closing
from pathlib import Path

from reviewproof import Item, Oversight, initialise
from reviewproof.config import load_config
from reviewproof.store import open_oversight_db, open_planted_db
from reviewproof.store.eventlog import verify

DEMO = Path(__file__).parents[1] / "examples" / "deviation_triage"
ITEMS = json.loads((DEMO / "items.json").read_text(encoding="utf-8"))
SCHEMA_ORDER = list(json.loads((DEMO / "item.schema.json").read_text("utf-8"))["properties"])
EXAMPLES = sorted((DEMO / "planted").glob("*.yaml"))
QUEUE = "deviation-triage"


class Deployment:
    def __init__(self, folder: Path, rate: float):
        self.folder = folder
        for name in ("reviewproof.yaml", "item.schema.json"):
            shutil.copy(DEMO / name, folder / name)
        config = load_config(folder / "reviewproof.yaml")
        queue = dataclasses.replace(config.queues[QUEUE], planted_rate_override=rate)
        self.config = dataclasses.replace(config, queues={QUEUE: queue})
        self.db = folder / "oversight.db"
        self.planted_db = folder / "planted.db"
        initialise(self.db, self.planted_db)
        self._open = []

    def open(self, seed=1, config=None, **arguments):
        rng = None if seed is None else random.Random(seed)
        ov = Oversight(config or self.config, QUEUE, self.db, self.planted_db, rng=rng, **arguments)
        self._open.append(ov)
        return ov

    def close(self):
        for ov in self._open:
            ov.close()

    def load_planted(self, ov):
        for path in EXAMPLES:
            planted = ov.planted.import_file(path)
            ov.planted.approve(planted.planted_error_id, "qa-1")
            ov.planted.approve(planted.planted_error_id, "qa-2")

    def query(self, sql, arguments=(), planted=False):
        path = self.planted_db if planted else self.db
        opener = open_planted_db if planted else open_oversight_db
        with closing(opener(path)) as db:
            return [dict(row) for row in db.execute(sql, arguments)]

    def kinds(self):
        rows = self.query("SELECT item_id, kind FROM item_registry", planted=True)
        return {row["item_id"]: row["kind"] for row in rows}

    def verify(self):
        with (
            closing(open_oversight_db(self.db)) as db,
            closing(open_planted_db(self.planted_db)) as registry,
        ):
            return verify(db, registry)


def item(number=0, **changes):
    payload = {**copy.deepcopy(ITEMS[number]), **changes}
    return Item(payload=payload, target_id=f"QMS-REC-{number:04d}")


def submit_until_pair(ov, start=0, reviewer=None):
    """Submit real items until one brings a planted item. Returns (real, planted) tickets."""
    for number in range(start, 20):
        tickets = ov.submit(item(number), reviewer)
        if len(tickets) == 2:
            real = next(t for t in tickets if t.payload == ITEMS[number])
            planted = next(t for t in tickets if t is not real)
            return real, planted
    raise AssertionError("no planted item was released")
