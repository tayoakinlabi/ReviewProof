"""The example planted errors and the authoring guide must keep working."""

import json
import re
import shutil
from pathlib import Path

import pytest

from reviewproof.config import load_config
from reviewproof.models import DefectClass
from reviewproof.planted import PlantedLibrary, Status
from reviewproof.strictload import load_file

ROOT = Path(__file__).parents[1]
DEMO = ROOT / "examples" / "deviation_triage"
GUIDE = ROOT / "docs" / "guides" / "writing-planted-errors.md"
EXAMPLES = sorted((DEMO / "planted").glob("*.yaml"))
ITEMS = json.loads((DEMO / "items.json").read_text(encoding="utf-8"))
QUEUE = "deviation-triage"

PRODUCT_LOTS = {
    "Examplol 50 mg tablets": "A",
    "Demozine 10 mg capsules": "B",
    "Placebrin 200 mg tablets": "C",
    "Mockaril 5 mg tablets": "D",
    "Synthavax diluent 2 mL vials": "E",
    "Fictamab 100 mg injection": "F",
}
CRITICAL_ACTIONS = {"Placed the batch on hold", "Notified the Quality Unit"}


def broken_rules(item):
    """The demo rules from the example's README that ``item`` breaks."""
    broken = []
    if item["product"] not in PRODUCT_LOTS:
        broken.append(1)
    elif item["lot_number"][4] != PRODUCT_LOTS[item["product"]]:
        broken.append(2)
    if item["capa_required"] != (item["classification"] != "minor"):
        broken.append(3)
    if (
        item["classification"] == "critical"
        and not set(item["immediate_actions"]) >= CRITICAL_ACTIONS
    ):
        broken.append(4)
    return broken


@pytest.fixture
def library(registry_db):
    return PlantedLibrary(registry_db, load_config(DEMO / "reviewproof.yaml"))


@pytest.mark.parametrize("item", ITEMS, ids=lambda item: item["deviation_id"])
def test_correct_demo_items_follow_the_demo_rules(item):
    assert broken_rules(item) == []


def test_the_demo_readme_lists_each_product_and_lot_series():
    readme = (DEMO / "README.md").read_text(encoding="utf-8")
    for product, series in PRODUCT_LOTS.items():
        assert f"| {product} | `LOT-{series}…` |" in readme
    for action in CRITICAL_ACTIONS:
        assert action in readme


def test_there_is_one_example_per_defect_class():
    classes = [load_file(path)["defect_class"] for path in EXAMPLES]
    assert sorted(classes) == sorted(member.value for member in DefectClass)


def test_every_example_imports_and_can_be_approved(library):
    for path in EXAMPLES:
        planted = library.import_file(path)
        library.approve(planted.planted_error_id, "qa-1")
        library.approve(planted.planted_error_id, "qa-2")
    assert {library.status(p) for p in library.all()} == {Status.APPROVED}
    assert len(library.available(QUEUE)) == len(EXAMPLES) == 6


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_each_example_says_it_is_synthetic_and_must_not_be_used(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# SYNTHETIC EXAMPLE")
    assert "Never use it" in text


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_examples_do_not_reuse_a_demo_identifier(path):
    payload = load_file(path)["payload"]
    for key in ("deviation_id", "lot_number"):
        assert payload[key] not in {item[key] for item in ITEMS}


@pytest.mark.parametrize(
    ("name", "rule"),
    [("wrong_value", 1), ("wrong_target", 2), ("policy_breach", 3), ("omission", 4)],
)
def test_rule_based_examples_have_exactly_one_detectable_defect(name, rule):
    data = load_file(DEMO / "planted" / f"{name}.yaml")
    assert broken_rules(data["payload"]) == [rule]
    fixed = {**data["payload"], data["defect_field"]: data["correct_value"]}
    assert broken_rules(fixed) == []


@pytest.mark.parametrize("name", ["wrong_classification", "unsupported_claim"])
def test_judgement_examples_break_no_mechanical_rule(name):
    # These are caught by judgement, so no rule should give them away.
    assert broken_rules(load_file(DEMO / "planted" / f"{name}.yaml")["payload"]) == []


# ----------------------------------------------------------------- the guide


def blocks(language):
    text = GUIDE.read_text(encoding="utf-8")
    return re.findall(rf"^```{language}\n(.*?)^```$", text, flags=re.MULTILINE | re.DOTALL)


def test_each_snippet_in_the_guide_matches_its_example_file(tmp_path):
    snippets = blocks("yaml")
    assert len(snippets) == len(DefectClass)
    for number, snippet in enumerate(snippets):
        path = tmp_path / f"{number}.yaml"
        path.write_text(snippet, encoding="utf-8")
        shown = load_file(path)
        actual = load_file(DEMO / "planted" / f"{shown['defect_class']}.yaml")
        shown_payload = shown.pop("payload")
        assert {key: actual[key] for key in shown} == shown
        assert {key: actual["payload"][key] for key in shown_payload} == shown_payload


def test_the_python_in_the_guide_runs(tmp_path, monkeypatch, capsys):
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)
    namespace = {}
    snippets = blocks("python")
    assert len(snippets) == 3
    try:
        for snippet in snippets:
            exec(compile(snippet, str(GUIDE), "exec"), namespace)  # noqa: S102
    finally:
        if "database" in namespace:
            namespace["database"].close()
    assert "DEMO-WV-1 approved" in capsys.readouterr().out
    draft = load_file(tmp_path / "PE-0007.yaml")
    assert (draft["correct_value"], draft["payload"]["classification"]) == ("critical", "major")


def test_the_guide_links_work():
    links = re.findall(r"\]\((?!https?://|#)([^)]+)\)", GUIDE.read_text(encoding="utf-8"))
    assert len(links) >= 8
    for link in links:
        assert (GUIDE.parent / link).exists(), link
