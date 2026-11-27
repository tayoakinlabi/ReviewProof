"""The validation pack hangs together: its IDs, its cross-references and its links.

This checks the documents against each other. It does not show that any requirement is met.
"""

import re
from pathlib import Path

import pytest

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
