"""Wrapping a plain approval function with ReviewProof. All data is synthetic.

Run from the repository root:  python examples/deviation_triage/integrate.py some-empty-folder

The three functions below stand in for what you already have. The integration is the few lines
between the two markers.
"""

import dataclasses
import json
import sys
from pathlib import Path

from reviewproof import Item, Oversight, initialise
from reviewproof.adapters import run_review
from reviewproof.config import load_config

HERE = Path(__file__).parent
QUEUE = "deviation-triage"


def approve(proposal):
    """Your approval step. This reviewer rubber-stamps everything."""
    return "approve"


def write_to_qms(target_id, payload):
    """Your executor. Also make it refuse the reserved target range."""
    if target_id.startswith("rp-reserved-"):
        raise PermissionError("reserved target ID")
    print(f"  wrote {payload['deviation_id']} to {target_id}")


def setup(folder, planted_rate):
    """Once per deployment. QA writes and approves planted errors (see the authoring guide)."""
    config = load_config(HERE / "reviewproof.yaml")
    queue = dataclasses.replace(config.queues[QUEUE], planted_rate_override=planted_rate)
    config = dataclasses.replace(config, queues={QUEUE: queue})
    db, planted_db = folder / "oversight.db", folder / "planted.db"
    initialise(db, planted_db)
    with Oversight(config, QUEUE, db, planted_db) as oversight:
        for path in sorted((HERE / "planted").glob("*.yaml")):
            planted = oversight.planted.import_file(path)
            oversight.planted.approve(planted.planted_error_id, "qa-lead-1")
            oversight.planted.approve(planted.planted_error_id, "qa-lead-2")
    return config, db, planted_db


def main(folder, ask=approve, executor=write_to_qms, planted_rate=0.2):
    # 20% so that a short demo shows planted errors. The queue's real setting is 2%.
    config, db, planted_db = setup(Path(folder), planted_rate)
    records = json.loads((HERE / "items.json").read_text(encoding="utf-8"))

    # --- integration start ---
    with Oversight(config, QUEUE, db, planted_db) as oversight:
        for number, proposal in enumerate(records):
            item = Item(payload=proposal, target_id=f"QMS-{number:04d}")
            run_review(oversight, item, "r-104", ask, executor)
    # --- integration end ---
    return db, planted_db


if __name__ == "__main__":
    main(sys.argv[1])
