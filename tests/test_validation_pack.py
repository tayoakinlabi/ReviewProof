"""The validation pack hangs together: its IDs, its cross-references and its links.

This checks the documents against each other. It does not show that any requirement is met.
"""

import re
import shlex
from math import comb
from pathlib import Path

import pytest

from reviewproof.cli.main import build_parser
from reviewproof.config import MAX_PLANTED_RATE, TIER_RATES, Thresholds

ROOT = Path(__file__).parents[1]
PACK = ROOT / "validation-pack"
DOCUMENTS = sorted(PACK.glob("*.md"))


def read(name):
    return (PACK / name).read_text(encoding="utf-8")


def defined(text, kind):
    """IDs that begin a table row, in order."""
    return re.findall(rf"^\| ({kind}-\d{{3}}) \|", text, flags=re.MULTILINE)


def mentioned(text, kind):
    """Every ID referred to, with ranges such as "REQ-014 to REQ-018" filled in."""
    found = set()
    for first, last in re.findall(rf"{kind}-(\d{{3}}) to {kind}-(\d{{3}})", text):
        found |= {f"{kind}-{n:03d}" for n in range(int(first), int(last) + 1)}
    return found | set(re.findall(rf"{kind}-\d{{3}}", text))


@pytest.mark.parametrize(
    ("document", "kind"),
    [("requirements.md", "REQ"), ("requirements.md", "DEP"), ("risk-assessment.md", "RISK")],
)
def test_ids_are_numbered_from_one_without_gaps_or_repeats(document, kind):
    ids = defined(read(document), kind)
    assert ids
    assert ids == [f"{kind}-{n:03d}" for n in range(1, len(ids) + 1)]


@pytest.mark.parametrize("kind", ["REQ", "DEP"])
def test_every_reference_is_to_something_defined(kind):
    known = set(defined(read("requirements.md"), kind))
    for path in DOCUMENTS:
        unknown = mentioned(path.read_text(encoding="utf-8"), kind) - known
        assert not unknown, f"{path.name} refers to {sorted(unknown)}"


@pytest.mark.parametrize("kind", ["REQ", "DEP"])
def test_every_requirement_and_duty_addresses_a_risk(kind):
    """One that no risk mentions is either unnecessary or shows a risk that was missed."""
    known = set(defined(read("requirements.md"), kind))
    unused = known - mentioned(read("risk-assessment.md"), kind)
    assert not unused, sorted(unused)


def test_every_risk_row_is_complete_and_left_for_the_reader_to_rate():
    rows = re.findall(r"^\| RISK-\d{3} \|.*$", read("risk-assessment.md"), flags=re.MULTILINE)
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        assert len(cells) == 8 and all(cells), cells[0]
        assert cells[-1] == "**[FILL]**", cells[0]


def anchors(text):
    """Heading anchors as GitHub makes them."""
    headings = re.findall(r"^#{1,6} +(.+?) *$", text, flags=re.MULTILINE)
    return {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: p.name)
def test_links_in_the_pack_work(path):
    text = path.read_text(encoding="utf-8")
    links = re.findall(r"\]\((?!https?://)([^)\s]+)\)", text.replace("\n", " "))
    for link in links:
        target, _, anchor = link.partition("#")
        file = (path.parent / target).resolve() if target else path
        assert file.exists(), link
        if anchor:
            assert anchor in anchors(file.read_text(encoding="utf-8")), link


# ----------------------------------------------------------------- test scripts


def test_every_deployment_duty_has_a_script_or_a_document_check():
    scripts = read("test-scripts.md")
    known = set(defined(read("requirements.md"), "DEP"))
    assert not known - mentioned(scripts, "DEP")


def test_manual_scripts_are_numbered_and_every_step_can_be_recorded():
    scripts = read("test-scripts.md")
    numbers = re.findall(r"^### (MT-\d{3}): ", scripts, flags=re.MULTILINE)
    assert numbers == [f"MT-{n:03d}" for n in range(1, len(numbers) + 1)]
    for section in re.split(r"^### MT-", scripts, flags=re.MULTILINE)[1:]:
        section = section.split("\n## ")[0]
        assert "| Step | Do | Expected result | Actual | Pass? |" in section, section[:40]
        steps = re.findall(r"^\| \d+ \|.*$", section, flags=re.MULTILINE)
        assert steps, section[:40]
        for step in steps:
            assert step.endswith("| | |"), step[:60]  # actual and pass are left empty


# ----------------------------------------------------------------- procedures


PROCEDURES = sorted(PACK.glob("sop-*.md"))


def commands(path):
    """The commands in a document's code blocks, without any redirection of their output."""
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"^ *```bash\n(.*?)^ *```", text, flags=re.MULTILINE | re.DOTALL)
    lines = (line.split(" > ")[0].strip() for block in blocks for line in block.splitlines())
    return [shlex.split(line) for line in lines if line]


def test_there_are_three_procedures_each_with_its_approval_left_to_fill_in():
    assert [p.stem for p in PROCEDURES] == [
        "sop-metric-review",
        "sop-planted-errors",
        "sop-reviewer-qualification",
    ]
    for path in PROCEDURES:
        header = path.read_text(encoding="utf-8").split("\n## ")[0]
        rows = re.findall(r"^\| [^|]+ \| \*\*\[FILL\]\*\* \|$", header, flags=re.MULTILINE)
        assert len(rows) == 5, path.name


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: p.name)
def test_every_rp_command_in_the_pack_is_one_the_tool_accepts(path):
    for command in commands(path):
        if command[0] == "rp":
            arguments = build_parser().parse_args(command[1:])  # exits if not accepted
            assert hasattr(arguments, "run"), command


def test_the_pack_runs_its_record_script_with_the_arguments_it_takes():
    found = [
        command[2:]
        for path in DOCUMENTS
        for command in commands(path)
        if command[:2] == ["python", "validation-pack/planted_records.py"]
    ]
    assert {(c[0], len(c)) for c in found} == {("register", 2), ("uses", 4), ("voids", 3)}


def test_the_chances_of_passing_are_right():
    text = read("sop-reviewer-qualification.md")
    pattern = r"^\| (\d+)% \| (\d+)% \| (\d+)% \| (\d+)% \|$"
    rows = re.findall(pattern, text, flags=re.MULTILINE)
    assert [row[0] for row in rows] == ["70", "80", "90", "95"]
    for row in rows:
        p = int(row[0]) / 100
        for mark, shown in zip((8, 9, 10), row[1:], strict=True):
            chance = sum(comb(10, k) * p**k * (1 - p) ** (10 - k) for k in range(mark, 11))
            assert round(100 * chance) == int(shown), (row[0], mark)


def test_the_procedures_quote_the_software_s_default_limits_and_shares():
    limits = Thresholds()
    review = read("sop-metric-review.md")
    for quoted in (
        f"Catch rate below {limits.catch_rate_min:.0%} over the latest {limits.rolling_window}",
        f"One kind of defect caught below {limits.class_catch_rate_min:.0%}",
        f"More than {limits.fast_approval_share_max:.0%} of a week's approvals",
        f"Over {limits.agreement_rate_max:.0%} agreement",
        f"Catch rate {limits.fatigue_drop_max * 100:.0f} points lower",
        f"No individual score appears below {limits.individual_min_planted} planted errors",
        f"than {limits.voided_share_max:.0%} of results are voided",
    ):
        assert quoted in review, quoted
    planted = " ".join(read("sop-planted-errors.md").split())
    low, middle, high = (f"{rate:.0%}" for rate in TIER_RATES)
    assert f"{low} of items for A1 to A4, {middle} for A5 and A6, {high} for A7" in planted
    assert f"At most `{MAX_PLANTED_RATE}`" in planted
