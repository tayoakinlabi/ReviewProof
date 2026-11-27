import copy
import dataclasses
import json
from pathlib import Path

import pytest

from reviewproof.config import load_config
from reviewproof.mutators import (
    DraftError,
    draft_classification_flip,
    draft_omission,
    draft_value_swap,
    write_draft,
)
from reviewproof.planted import PlantedLibrary, Status
from reviewproof.strictload import load_file

DEMO = Path(__file__).parents[1] / "examples" / "deviation_triage"
ITEMS = json.loads((DEMO / "items.json").read_text(encoding="utf-8"))
CONFIG = load_config(DEMO / "reviewproof.yaml")
QUEUE = CONFIG.queues["deviation-triage"]
WHO = {"planted_error_id": "PE-1", "author": "author-1"}

HELPERS = {
    "value swap": (draft_value_swap, {"field": "lot_number", "new_value": "LOT-Z99999"}),
    "omission": (draft_omission, {"field": "immediate_actions", "index": 0}),
    "classification flip": (draft_classification_flip, {"field": "classification"}),
}


def changed_fields(source, payload):
    return {key for key in source.keys() | payload.keys() if source.get(key) != payload.get(key)}


@pytest.mark.parametrize("helper", HELPERS)
@pytest.mark.parametrize("item", ITEMS, ids=lambda item: item["deviation_id"])
def test_a_draft_changes_only_the_declared_field_and_imports(registry_db, helper, item):
    function, arguments = HELPERS[helper]
    source = copy.deepcopy(item)
    draft = function(QUEUE, source, **WHO, **arguments)

    assert source == item  # the caller's item is not touched
    assert changed_fields(item, draft["payload"]) == {draft["defect_field"]}
    assert draft["correct_value"] == item[draft["defect_field"]]
    assert draft["expected_decision"] == f"edit:{draft['defect_field']}"

    library = PlantedLibrary(registry_db, CONFIG)
    planted = library.import_data(draft)
    assert library.status(planted) is Status.AWAITING_APPROVAL
    assert library.available(QUEUE.name) == []


@pytest.mark.req("REQ-005")
@pytest.mark.parametrize("helper", HELPERS)
def test_a_draft_shares_nothing_with_its_source(helper):
    function, arguments = HELPERS[helper]
    source = copy.deepcopy(ITEMS[0])
    draft = function(QUEUE, source, **WHO, **arguments)
    draft["payload"]["immediate_actions"].append("added later")
    assert source == ITEMS[0]


@pytest.mark.parametrize(
    ("current", "to", "expected"),
    [
        ("critical", None, "major"),
        ("major", None, "minor"),
        ("minor", None, "major"),
        ("major", "critical", "critical"),
    ],
)
def test_classification_flip_targets(current, to, expected):
    source = next(item for item in ITEMS if item["classification"] == current)
    draft = draft_classification_flip(QUEUE, source, **WHO, field="classification", to=to)
    assert draft["payload"]["classification"] == expected


def test_omission_of_an_optional_property(tmp_path):
    schema = json.loads(QUEUE.item_schema.read_text(encoding="utf-8"))
    schema["required"].remove("root_cause")
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    queue = dataclasses.replace(QUEUE, item_schema=path)
    draft = draft_omission(queue, ITEMS[0], **WHO, field="root_cause")
    assert "root_cause" not in draft["payload"]
    assert draft["correct_value"] == ITEMS[0]["root_cause"]


def test_reject_can_be_the_expected_decision():
    draft = draft_value_swap(
        QUEUE,
        ITEMS[0],
        **WHO,
        field="lot_number",
        new_value="LOT-Z99999",
        expected_decision="reject",
    )
    assert draft["expected_decision"] == "reject"


@pytest.mark.parametrize(
    ("function", "arguments", "message"),
    [
        (draft_value_swap, {"field": "nope", "new_value": 1}, "not a property"),
        (draft_value_swap, {"field": "lot_number", "new_value": "LOT-A10231"}, "same as"),
        (draft_value_swap, {"field": "lot_number", "new_value": "bad"}, "result would not match"),
        (
            draft_value_swap,
            {"field": "product", "new_value": "x", "expected_decision": "approve"},
            "expected_decision",
        ),
        (draft_omission, {"field": "immediate_actions"}, "give the index"),
        (draft_omission, {"field": "root_cause"}, "result would not match"),
        (draft_omission, {"field": "root_cause", "index": 0}, "not a list"),
        (draft_omission, {"field": "immediate_actions", "index": 3}, "from 0 to 2"),
        (draft_omission, {"field": "immediate_actions", "index": -1}, "from 0 to 2"),
        (draft_omission, {"field": "immediate_actions", "index": True}, "from 0 to 2"),
        (draft_classification_flip, {"field": "root_cause"}, "needs an enum"),
        (draft_classification_flip, {"field": "classification", "to": "huge"}, "must be one of"),
        (draft_classification_flip, {"field": "classification", "to": "minor"}, "same as"),
    ],
)
def test_bad_requests_are_refused(function, arguments, message):
    with pytest.raises(DraftError, match=message):
        function(QUEUE, ITEMS[0], **WHO, **arguments)


def test_removing_the_last_list_entry_is_refused():
    source = {**ITEMS[0], "immediate_actions": ["only one"]}
    with pytest.raises(DraftError, match="needs at least 1"):
        draft_omission(QUEUE, source, **WHO, field="immediate_actions", index=0)


def test_a_source_that_does_not_match_the_schema_is_refused():
    with pytest.raises(DraftError, match="source item does not match"):
        draft_value_swap(QUEUE, {"product": "x"}, **WHO, field="product", new_value="y")


def test_a_field_absent_from_the_source_is_refused(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text('{"properties": {"a": {}, "b": {}}}', encoding="utf-8")
    queue = dataclasses.replace(QUEUE, item_schema=path)
    with pytest.raises(DraftError, match="has no 'b'"):
        draft_value_swap(queue, {"a": 1}, **WHO, field="b", new_value=2)


def test_written_drafts_load_back_unchanged_and_carry_a_checklist(tmp_path):
    draft = draft_classification_flip(QUEUE, ITEMS[0], **WHO, field="classification")
    path = tmp_path / "draft.yaml"
    write_draft(draft, path)
    assert load_file(path) == draft
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# DRAFT")
    assert "[ ] No real data" in text


def test_writing_never_overwrites(tmp_path):
    path = tmp_path / "draft.yaml"
    path.write_text("keep me", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_draft({}, path)
    assert path.read_text(encoding="utf-8") == "keep me"
