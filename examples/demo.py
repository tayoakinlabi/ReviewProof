"""The end-to-end demo: two simulated reviewers, one careful and one rubber-stamping.

    python examples/demo.py [FOLDER] [--seed N]

It sets up a queue, has QA approve planted errors, lets both reviewers work for a simulated
week, verifies the log and writes the QA report. **Everything is synthetic.** The reviewers'
behaviour is assumed, so these numbers are not results about people.
"""

import argparse
import copy
import json
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reviewproof import Item, Oversight, initialise
from reviewproof.config import load_config
from reviewproof.metrics.projection import load
from reviewproof.mutators import draft_classification_flip, draft_omission, draft_value_swap
from reviewproof.report import build, html, tables
from reviewproof.simulator import (
    CAREFUL,
    RUBBER_STAMP,
    SimulatedClock,
    SimulatedReviewer,
    run_session,
)

DEMO = Path(__file__).parent / "deviation_triage"
QUEUE = "deviation-triage"
ITEMS = json.loads((DEMO / "items.json").read_text(encoding="utf-8"))
#: The real setting is 1 to 3%. 15% gives each reviewer enough planted errors in one week.
DEMO_PLANTED_RATE = 0.15
DAYS = 5
ITEMS_PER_DAY = 40
MONDAY = datetime(2026, 3, 2, tzinfo=timezone.utc)
REVIEWERS = {"r-101": (CAREFUL, 9), "r-102": (RUBBER_STAMP, 13)}  # ID: profile, start hour


def write_settings(folder: Path) -> Path:
    text = (DEMO / "reviewproof.yaml").read_text(encoding="utf-8")
    marker = "    min_review_seconds: 30\n"
    text = text.replace(marker, marker + f"    planted_rate: {DEMO_PLANTED_RATE}\n")
    path = folder / "reviewproof.yaml"
    path.write_text(text, encoding="utf-8")
    (folder / "item.schema.json").write_text(
        (DEMO / "item.schema.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return path


def synthetic_planted_errors(queue):
    """The six hand-written examples, plus 80 made with the drafting helpers. A real queue
    needs planted errors written and checked by people: see the authoring guide.
    """
    yield from sorted((DEMO / "planted").glob("*.yaml"))
    number = 0
    for source in ITEMS:
        lot = source["lot_number"]
        other_series = "ABCDEF"[("ABCDEF".index(lot[4]) + 1) % 6]
        recipes = [
            (draft_classification_flip, {"field": "classification"}, None),
            (draft_omission, {"field": "immediate_actions", "index": 0}, None),
            (
                draft_value_swap,
                {"field": "lot_number", "new_value": f"LOT-{other_series}{lot[5:]}"},
                "wrong_target",
            ),
            (
                draft_value_swap,
                {"field": "capa_required", "new_value": not source["capa_required"]},
                "policy_breach",
            ),
        ]
        for helper, arguments, defect_class in recipes:
            number += 1
            draft = helper(
                queue,
                copy.deepcopy(source),
                planted_error_id=f"DEMO-{number:03d}",
                author="demo-author",
                **arguments,
            )
            if defect_class:
                draft["defect_class"] = defect_class
            draft["payload"]["deviation_id"] = f"DEV-2026-{9000 + number:04d}"
            yield draft


def proposals(first: int, count: int):
    """Stand-ins for what an AI system would propose."""
    for number in range(first, first + count):
        payload = {**ITEMS[number % len(ITEMS)], "deviation_id": f"DEV-2025-{number:04d}"}
        yield Item(payload=payload, target_id=f"QMS-{number:05d}")


def executor(target_id, payload):
    """Stands in for the quality system. It refuses the reserved range, as yours should."""
    if target_id.startswith("rp-reserved-"):
        raise PermissionError("reserved target ID")


def run(folder: Path, seed: int = 1, days: int = DAYS, per_day: int = ITEMS_PER_DAY):
    folder.mkdir(parents=True, exist_ok=True)
    db, planted_db = folder / "oversight.db", folder / "planted.db"
    if db.exists() or planted_db.exists():
        raise SystemExit(f"{folder} already holds a demo. Choose an empty folder.")
    settings = write_settings(folder)
    config = load_config(settings)
    initialise(db, planted_db)

    clock = SimulatedClock(MONDAY.replace(hour=7))
    rng = random.Random(seed)
    with Oversight(
        config, QUEUE, db, planted_db, rng=random.Random(rng.random()), clock=clock
    ) as oversight:
        library = oversight.planted
        for source in synthetic_planted_errors(config.queues[QUEUE]):
            planted = (
                library.import_file(source)
                if isinstance(source, Path)
                else library.import_data(source)
            )
            library.approve(planted.planted_error_id, "demo-qa-1")
            library.approve(planted.planted_error_id, "demo-qa-2")

        reviewers = {
            reviewer_id: SimulatedReviewer(
                reviewer_id, profile, library.all(), random.Random(rng.random())
            )
            for reviewer_id, (profile, _) in REVIEWERS.items()
        }
        pauses = random.Random(rng.random())
        submitted = 0
        for day in range(days):
            for reviewer_id, (_, hour) in REVIEWERS.items():
                clock.move_to(MONDAY + timedelta(days=day, hours=hour))
                run_session(
                    oversight,
                    reviewers[reviewer_id],
                    proposals(submitted, per_day),
                    clock,
                    executor,
                    rng=pauses,
                )
                submitted += per_day

    projection = load(db, planted_db)  # verifies the log and the item records first
    report = build(
        projection,
        config,
        planted_db,
        individuals=True,
        now=clock.now + timedelta(hours=1),
    )
    (folder / "report.html").write_text(html.render(report), encoding="utf-8")
    (folder / "report.csv").write_text(tables.render(report), encoding="utf-8", newline="")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the ReviewProof demo (synthetic data).")
    parser.add_argument("folder", nargs="?", help="an empty folder for the demo's files")
    parser.add_argument("--seed", type=int, default=1, help="default: %(default)s")
    args = parser.parse_args(argv)
    folder = Path(args.folder or tempfile.mkdtemp(prefix="reviewproof-demo-"))

    print("Running the demo with invented data and simulated reviewers...")
    report = run(folder, args.seed)
    [queue] = report.queues
    print(f"\nEvidence verified: {report.head.event_count} log entries.")
    flagged = {}
    for person in queue.reviewers:
        profile = REVIEWERS[person.reviewer][0].name
        texts = [flag.text for flag in person.flags]
        flagged[profile] = bool(texts)
        caught = person.summary.catch
        print(
            f"  {person.reviewer} ({profile} simulation): caught {caught.successes} of "
            f"{caught.total}. {'FLAGGED: ' + '; '.join(texts) if texts else 'Nothing flagged.'}"
        )
    print(f"\nOpen the report:  {folder / 'report.html'}")
    print(f"As a table:       {folder / 'report.csv'}")
    print(
        f"Check the log:    rp verify --db {folder / 'oversight.db'} "
        f"--planted {folder / 'planted.db'}"
    )
    expected = flagged == {"careful": False, "rubber-stamp": True}
    if not expected:
        print(
            "\nThis seed did not give the expected flags. With limited planted errors, chance "
            "can put a careful reviewer below target."
        )
    return 0 if expected else 1


if __name__ == "__main__":
    sys.exit(main())
