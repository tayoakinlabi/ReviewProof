"""The Part 11 control table and the VITAL mapping (step 3.6).

What they say the software does **not** do is tested too. If one of these tests fails because
the software now does more, correct the document.
"""

import re
from contextlib import closing
from pathlib import Path

import pytest
from deploy import EXAMPLES, item, submit_until_pair
from helpers import triggers_removed

from reviewproof.config import ConfigError, risk_tier
from reviewproof.models import RiskCategory
from reviewproof.store import open_oversight_db, open_planted_db

PACK = Path(__file__).parents[1] / "validation-pack"
PART11 = (PACK / "part11-controls.md").read_text(encoding="utf-8")
VITAL = (PACK / "vital-mapping.md").read_text(encoding="utf-8")


def table_rows(text, first_cell):
    """The cells of each table row whose first cell matches ``first_cell``."""
    found = []
    for line in text.splitlines():
        if line.startswith("| "):
            cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
            if re.fullmatch(first_cell, cells[0]):
                found.append(cells)
    return found


def section(text, heading):
    return text.split(f"\n## {heading}")[1].split("\n## ")[0]


# ----------------------------------------------------------------- Part 11


def test_every_closed_system_control_has_a_row_left_for_the_reader():
    rows = table_rows(section(PART11, "2. "), r"\([a-k]\)(\(\d\))?( †)?")
    clauses = [row[0].removesuffix(" †") for row in rows]
    assert clauses == [*(f"({letter})" for letter in "abcdefghij"), "(k)(1)", "(k)(2)"]
    for row in rows:
        assert len(row) == 5 and all(row), row[0]
        assert row[-1].endswith("**[FILL]**"), row[0]


def test_the_marked_clauses_are_those_the_2003_guidance_names():
    marked = [r[0] for r in table_rows(section(PART11, "2. "), r"\([a-k]\)(\(\d\))? †")]
    assert marked == ["(a) †", "(b) †", "(c) †", "(e) †", "(k)(2) †"]


def test_every_signature_section_says_it_is_not_provided():
    rows = table_rows(section(PART11, "5. "), r"§ 11\.\d+")
    assert [row[0] for row in rows] == [f"§ 11.{n}" for n in (50, 70, 100, 200, 300)]
    for row in rows:
        assert row[2].startswith("Not provided") and row[3] == "**[FILL]**", row[0]
    assert "## 4. Open systems, § 11.30" in PART11


# ----------------------------------------------------------------- what verification misses


@pytest.fixture
def used_and_unused(make):
    """One planted error released into the queue, and one approved but never used."""
    deployment = make()
    ov = deployment.open()
    ids = []
    for path in EXAMPLES[:1]:
        ids.append(ov.planted.import_file(path).planted_error_id)
        for who in ("qa-1", "qa-2"):
            ov.planted.approve(ids[-1], who)
    submit_until_pair(ov)
    unused = ov.planted.import_file(EXAMPLES[1]).planted_error_id
    ov.planted.approve(unused, "qa-1")
    return deployment, ids[0], unused


def change(path, opener, table, statement, arguments=()):
    with closing(opener(path)) as db, triggers_removed(db, table):
        assert db.execute(statement, arguments).rowcount >= 1


@pytest.mark.req("REQ-027")
def test_a_changed_approval_is_found_once_the_planted_error_has_been_used(used_and_unused):
    deployment, used, _ = used_and_unused
    change(
        deployment.planted_db,
        open_planted_db,
        "planted_errors",
        "UPDATE planted_errors SET approved_by = 'someone' WHERE planted_error_id = ?",
        (used,),
    )
    assert not deployment.verify().ok


def test_a_changed_approval_is_not_found_before_it_is_used(used_and_unused):
    deployment, _, unused = used_and_unused
    change(
        deployment.planted_db,
        open_planted_db,
        "planted_errors",
        "UPDATE planted_errors SET approved_by = 'someone' WHERE planted_error_id = ?",
        (unused,),
    )
    assert deployment.verify().ok


def test_changed_retirement_details_are_not_found(used_and_unused):
    deployment, used, _ = used_and_unused
    deployment.open().planted.retire(used, "qa-1", "recognised")
    change(
        deployment.planted_db,
        open_planted_db,
        "planted_errors",
        "UPDATE planted_errors SET retired_by = 'someone', retire_reason = 'other' "
        "WHERE planted_error_id = ?",
        (used,),
    )
    assert deployment.verify().ok


def test_removed_shortfalls_are_not_found(make):
    deployment = make()
    ov = deployment.open()
    for number in range(20):  # with no planted errors, any that falls due is a shortfall
        ov.submit(item(number))
    with closing(open_planted_db(deployment.planted_db)) as db:
        assert db.execute("SELECT COUNT(*) FROM gate_shortfalls").fetchone()[0] > 0
        db.execute("DELETE FROM gate_shortfalls")
    assert deployment.verify().ok


def test_a_changed_reviewer_status_table_is_not_found(make):
    deployment = make()
    ov = deployment.open()
    [ticket, *_] = ov.submit(item(0))
    ov.shown(ticket.token, "r-1")
    with closing(open_oversight_db(deployment.db)) as db:
        changed = db.execute("UPDATE reviewers SET qualification_status = 'qualified'")
        assert changed.rowcount == 1
    assert deployment.verify().ok


# ----------------------------------------------------------------- VITAL


CATEGORIES = [
    "Context-of-use specification",
    "Governance operating model",
    "Data specification",
    "Design specifications and controls",
    "Validation and revalidation protocol",
    "Validation evidence package",
    "Traceability and logging specification",
    "Operational monitoring plan",
    "Third-party dependency register",
    "Transparency and auditability specification",
]


def test_the_ten_categories_are_mapped_and_the_summary_counts_them_right():
    rows = table_rows(section(VITAL, "The ten categories"), "|".join(CATEGORIES))
    assert [row[0] for row in rows] == CATEGORIES
    for row in rows:
        assert len(row) == 5 and all(row) and row[-1] == "**[FILL]**", row[0]
    nothing = [row[0] for row in rows if row[2].startswith("**Nothing")]
    assert len(nothing) == 3 and len(rows) - len(nothing) == 7
    assert "contribute part of seven" in VITAL and "nothing to three" in VITAL


def test_the_rating_advice_matches_what_the_settings_accept():
    advice = section(VITAL, "Recording your VITAL risk rating")
    named = set(re.findall(r"`vital_adjusted_category: (\w+)`", advice))
    assert named == {category.value for category in RiskCategory}
    for step in range(1, 11):
        risk_tier(f"A{step}")
    with pytest.raises(ConfigError):
        risk_tier("A5.5")  # "Half steps are refused"
    assert risk_tier("A4", RiskCategory.HIGH) == risk_tier("A4") + 1
