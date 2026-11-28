"""The pack's script for what the ``rp`` commands do not show: planted_records.py.

The procedure templates rely on it to find the results a faulty planted error affected.
"""

import csv
import importlib.util
import io
import sys
from pathlib import Path

import golden_log
import pytest
from deploy import EXAMPLES, submit_until_pair
from qual_support import load_pool, plan, sit

from reviewproof import initialise
from reviewproof.store import schema

ROOT = Path(__file__).parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "planted_records", ROOT / "validation-pack" / "planted_records.py"
)
records = importlib.util.module_from_spec(_SPEC)
sys.modules["planted_records"] = records
_SPEC.loader.exec_module(records)


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    return golden_log.build(tmp_path_factory.mktemp("golden-records"))


def run(capsys, *arguments):
    status = records.main([str(a) for a in arguments])
    out, err = capsys.readouterr()
    return status, list(csv.DictReader(io.StringIO(out))), out + err


def test_the_register_says_who_and_why_and_shows_no_content(golden, capsys):
    status, rows, output = run(capsys, "register", golden.planted_db)
    assert status == 0 and len(rows) == 12
    first, third = rows[0], rows[2]
    assert (first["planted_error_id"], first["author"]) == ("PE-01", "author-1")
    assert (first["approved_by"], first["second_approved_by"]) == ("qa-1", "qa-2")
    assert first["retired_by"] == "" and third["retired_by"] == "qa-1" and third["retired_at"]
    for content in ("DEV-2025", "LOT-", "rp-reserved"):
        assert content not in output


def test_every_use_of_a_planted_error_is_found_with_how_it_scored(golden, capsys):
    # PE-01 was released three times: P01, decided by A; Q01, by B; and X01, by B again.
    status, rows, _ = run(capsys, "uses", "PE-01", golden.db, golden.planted_db)
    assert status == 0
    decided = [r for r in rows if r["what_happened"] == "decided"]
    assert [(r["use"], r["reviewer"], r["decision"], r["scored_as"]) for r in decided] == [
        ("live", "A", "reject", "catch"),
        ("live", "B", "approve", "miss"),
        ("live", "B", "approve", "miss"),  # a repeat: it counts in no score, but is listed
    ]
    assert [r["reference"] for r in decided] == [golden.ids[n] for n in ("P01", "Q01", "X01")]
    assert [r["time_utc"] for r in rows] == sorted(r["time_utc"] for r in rows)


def test_retiring_leaves_the_results_where_they_were(golden, capsys):
    # The golden history retires PE-03 after A decided on it: that result is still listed,
    # and the procedures say it still counts.
    _, rows, _ = run(capsys, "uses", "PE-03", golden.db, golden.planted_db)
    assert [r["reviewer"] for r in rows if r["what_happened"] == "decided"] == ["A"]


def test_uses_in_a_qualification_session_are_found(make, capsys):
    deployment = make(rate=0.001)
    ov = deployment.open()
    pool = load_pool(ov, range(12))
    qualification = ov.qualification()
    session = qualification.start("r-7", plan(pool))
    chosen = plan(pool).planted_errors[0]

    _, rows, _ = run(capsys, "uses", chosen, deployment.db, deployment.planted_db)
    # Nothing is recorded about this item, but the person who has the worksheet is known.
    assert [(r["use"], r["reviewer"], r["what_happened"]) for r in rows] == [
        ("qualification", "r-7", "nothing recorded")
    ]

    sit(qualification, session)
    _, rows, _ = run(capsys, "uses", chosen, deployment.db, deployment.planted_db)
    [answer] = [r for r in rows if r["what_happened"] == "qualification_answered"]
    assert (answer["reference"], answer["reviewer"]) == (session.session_id, "r-7")
    assert answer["scored_as"] == "catch"


def test_an_abandoned_session_still_names_who_had_the_item(make, capsys):
    deployment = make(rate=0.001)
    ov = deployment.open()
    pool = load_pool(ov, range(12))
    qualification = ov.qualification()
    session = qualification.start("r-8", plan(pool))
    qualification.abandon(session.session_id)
    chosen = plan(pool).planted_errors[0]
    _, rows, _ = run(capsys, "uses", chosen, deployment.db, deployment.planted_db)
    assert [(r["reference"], r["reviewer"]) for r in rows] == [(session.session_id, "r-8")]


@pytest.mark.parametrize("named", ["r-9", None])
def test_a_waiting_live_copy_names_the_reviewer_if_the_application_did(make, capsys, named):
    deployment = make()
    ov = deployment.open()
    planted = ov.planted.import_file(EXAMPLES[0])
    for who in ("qa-1", "qa-2"):
        ov.planted.approve(planted.planted_error_id, who)
    submit_until_pair(ov, reviewer=named)
    _, rows, _ = run(capsys, "uses", planted.planted_error_id, deployment.db, deployment.planted_db)
    assert [(r["use"], r["reviewer"], r["what_happened"]) for r in rows] == [
        ("live", named or "", "nothing recorded")
    ]


def test_every_void_is_listed_with_what_became_of_it(make, capsys):
    deployment = make(rate=0.001)
    ov = deployment.open()
    pool = load_pool(ov, range(12))
    qualification = ov.qualification()
    sit(qualification, qualification.start("r-7", plan(pool)))
    voids = ov.voids()
    voids.request(pool[0], "qa-1", "not_defective", "=cmd()", "DEV-1")
    voids.confirm(pool[0], "qa-lead")
    voids.request(pool[1], "qa-1", "not_findable", "second", "DEV-2")
    voids.cancel(pool[1], "qa-8")
    voids.request(pool[2], "qa-1", "more_than_one_defect", "third", "DEV-3")
    files = (deployment.db, deployment.planted_db)
    before = [path.read_bytes() for path in files]

    status, rows, _ = run(capsys, "voids", *files)

    assert status == 0
    assert [(r["planted_error_id"], r["state"], r["reason"]) for r in rows] == [
        (pool[0], "in_effect", "not_defective"),
        (pool[1], "cancelled", "not_findable"),
        (pool[2], "awaiting_confirmation", "more_than_one_defect"),
    ]
    first, second, _third = rows
    assert (first["requested_by"], first["confirmed_by"]) == ("qa-1", "qa-lead")
    assert second["cancelled_by"] == "qa-8" and second["confirmed_by"] == ""
    assert first["explanation"] == "'=cmd()"  # never a spreadsheet formula
    assert [path.read_bytes() for path in files] == before


def test_a_database_not_yet_upgraded_has_no_voids(tmp_path, monkeypatch, capsys):
    with monkeypatch.context() as old:
        old.setattr(schema, "OVERSIGHT_MIGRATIONS", schema.OVERSIGHT_MIGRATIONS[:3])
        old.setattr(schema, "PLANTED_MIGRATIONS", schema.PLANTED_MIGRATIONS[:2])
        initialise(tmp_path / "oversight.db", tmp_path / "planted.db")
    status, rows, _ = run(capsys, "voids", tmp_path / "oversight.db", tmp_path / "planted.db")
    assert status == 0 and rows == []


def test_it_changes_neither_database(golden, capsys):
    before = {p: p.read_bytes() for p in (golden.db, golden.planted_db)}
    run(capsys, "register", golden.planted_db)
    run(capsys, "uses", "PE-01", golden.db, golden.planted_db)
    assert {p: p.read_bytes() for p in (golden.db, golden.planted_db)} == before


@pytest.mark.parametrize(
    "arguments",
    [("uses", "PE-99", "DB", "PLANTED"), ("register", "missing.db"), ("nonsense",), ()],
)
def test_a_mistake_is_reported_without_creating_anything(
    golden, capsys, tmp_path, monkeypatch, arguments
):
    monkeypatch.chdir(tmp_path)
    paths = {"DB": golden.db, "PLANTED": golden.planted_db}
    status, rows, _ = run(capsys, *(paths.get(a, a) for a in arguments))
    assert status == 2 and rows == []
    assert list(tmp_path.iterdir()) == []


def test_a_value_cannot_become_a_spreadsheet_formula():
    assert records._safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert records._safe(None) == ""
