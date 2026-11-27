"""Gate 2: the synthetic demo runs end to end, and the report flags the rubber-stamping
simulated reviewer while the careful one passes. The simulated behaviour is assumed: this shows
the software works, not that the method does.
"""

import csv
import importlib.util
import time
from contextlib import closing
from pathlib import Path

import pytest

from reviewproof.metrics.measures import catch_rate, median_dwell_ms
from reviewproof.metrics.projection import load
from reviewproof.store import open_oversight_db, open_planted_db
from reviewproof.store.eventlog import verify

ROOT = Path(__file__).parents[2]


def load_demo():
    spec = importlib.util.spec_from_file_location("demo_script", ROOT / "examples" / "demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEMO = load_demo()


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    folder = tmp_path_factory.mktemp("gate2")
    started = time.perf_counter()
    report = DEMO.run(folder)
    return folder, report, time.perf_counter() - started


def people(report):
    [queue] = report.queues
    return {DEMO.REVIEWERS[p.reviewer][0].name: p for p in queue.reviewers}


def test_the_rubber_stamping_reviewer_is_flagged(run):
    _, report, _ = run
    flags = {f.kind for f in people(report)["rubber-stamp"].flags}
    assert flags == {"catch_clearly_below", "fast_approvals"}


def test_the_careful_reviewer_passes(run):
    _, report, _ = run
    careful = people(report)["careful"]
    assert careful.summary is not None  # enough planted errors to have been judged
    assert careful.flags == ()


def test_the_simulated_numbers_are_in_the_expected_ranges(run):
    folder, _, _ = run
    projection = load(folder / "oversight.db", folder / "planted.db")
    by_reviewer = {}
    for record in projection.records:
        by_reviewer.setdefault(record.reviewer, []).append(record)
    careful, rubber = by_reviewer["r-101"], by_reviewer["r-102"]
    assert 0.8 <= catch_rate(careful).value <= 1.0
    assert 0.15 <= catch_rate(rubber).value <= 0.45
    assert 70_000 <= median_dwell_ms(careful) <= 115_000
    assert 6_000 <= median_dwell_ms(rubber) <= 11_000


def test_the_run_produces_a_report_a_table_and_verifiable_evidence(run):
    folder, report, _ = run
    page = (folder / "report.html").read_text(encoding="utf-8")
    assert "Needs attention" in page and "Catch rate clearly below target" in page
    with (folder / "report.csv").open(encoding="utf-8", newline="") as file:
        flagged = {
            row["scope"] for row in csv.DictReader(file) if row["metric"].startswith("flag_")
        }
    assert flagged == {"r-102"}
    with (
        closing(open_oversight_db(folder / "oversight.db")) as db,
        closing(open_planted_db(folder / "planted.db")) as planted,
    ):
        assert verify(db, planted).ok
    assert report.queues[0].programme.shortfalls == 0


def test_no_planted_error_reached_the_executor(run):
    folder, _, _ = run
    projection = load(folder / "oversight.db", folder / "planted.db")
    assert not any(r.executed for r in projection.records if r.planted)
    assert not projection.refusals
    assert sum(r.executed for r in projection.records) > 300


def test_the_demo_takes_well_under_fifteen_minutes(run):
    assert run[2] < 300


def test_the_command_line_entry_point(tmp_path, capsys):
    assert DEMO.main([str(tmp_path / "new")]) == 0
    out = capsys.readouterr().out
    assert "r-101 (careful simulation)" in out and "Nothing flagged." in out
    assert "FLAGGED: Catch rate clearly below target" in out
    with pytest.raises(SystemExit, match="already holds a demo"):
        DEMO.main([str(tmp_path / "new")])


def test_an_unexpected_outcome_is_reported_not_hidden(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(DEMO, "REVIEWERS", {"r-102": DEMO.REVIEWERS["r-102"]})
    assert DEMO.main([str(tmp_path / "one")]) == 1
    assert "did not give the expected flags" in capsys.readouterr().out
