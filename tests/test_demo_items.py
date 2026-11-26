import json
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

DEMO = Path(__file__).parents[1] / "examples" / "deviation_triage"
SCHEMA = json.loads((DEMO / "item.schema.json").read_text(encoding="utf-8"))
ITEMS = json.loads((DEMO / "items.json").read_text(encoding="utf-8"))


def test_schema_is_valid():
    Draft202012Validator.check_schema(SCHEMA)


def test_there_are_20_items_with_unique_ids():
    assert len(ITEMS) == 20
    assert len({item["deviation_id"] for item in ITEMS}) == 20


@pytest.mark.parametrize("item", ITEMS, ids=lambda item: item["deviation_id"])
def test_item_matches_schema(item):
    Draft202012Validator(SCHEMA).validate(item)
    date.fromisoformat(item["occurred_on"])


def test_every_classification_is_represented():
    expected = set(SCHEMA["properties"]["classification"]["enum"])
    assert {item["classification"] for item in ITEMS} == expected


def test_every_item_supports_an_omission_defect():
    assert all(len(item["immediate_actions"]) >= 2 for item in ITEMS)
