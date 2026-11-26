import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from reviewproof.itemschema import ItemSchema, SchemaError, json_equal

DEMO = Path(__file__).parents[1] / "examples" / "deviation_triage"
DEMO_SCHEMA = json.loads((DEMO / "item.schema.json").read_text(encoding="utf-8"))
ITEMS = json.loads((DEMO / "items.json").read_text(encoding="utf-8"))

# Values of every JSON type, including the awkward ones.
VALUES = [
    None,
    True,
    False,
    0,
    1,
    -1,
    1.0,
    1.5,
    "",
    "a",
    "abc",
    "minor",
    "µg",
    [],
    [1],
    [True],
    ["a", "b"],
    [1, 2, 3],
    {},
    {"a": 1},
    {"a": "x", "b": 2},
]

SCHEMAS = [
    True,
    False,
    {},
    *[{"type": name} for name in ("object", "array", "string", "number", "integer")],
    {"type": "boolean"},
    {"type": "null"},
    {"type": ["string", "null"]},
    {"enum": [1, "a", None]},
    {"enum": [True]},
    {"enum": [0, [1], {"a": 1}]},
    {"pattern": "^a"},
    {"pattern": "b"},
    {"pattern": "^[a-z]+$"},
    {"minLength": 2},
    {"maxLength": 1},
    {"minimum": 1},
    {"maximum": 0},
    {"minItems": 2},
    {"maxItems": 1},
    {"items": {"type": "integer"}},
    {"items": False},
    {"required": ["a"]},
    {"properties": {"a": {"type": "integer"}}},
    {"properties": {"a": {}}, "additionalProperties": False},
    {"properties": {"a": {}}, "additionalProperties": {"type": "integer"}},
    {"additionalProperties": False},
    {"type": "object", "properties": {"b": False}},
]


@pytest.mark.parametrize("schema", SCHEMAS, ids=json.dumps)
def test_agrees_with_jsonschema(schema):
    ours, reference = ItemSchema(schema), Draft202012Validator(schema)
    for value in VALUES:
        assert (not ours.errors(value)) == reference.is_valid(value), value


def variations(item):
    """Many ways of damaging a good item."""
    for name in item:
        without = copy.deepcopy(item)
        del without[name]
        yield without
        for value in VALUES:
            yield {**item, name: value}
    yield {**item, "extra": 1}


def test_agrees_with_jsonschema_on_damaged_demo_items():
    ours, reference = ItemSchema(DEMO_SCHEMA), Draft202012Validator(DEMO_SCHEMA)
    checked = 0
    for item in ITEMS[:3]:
        for value in variations(item):
            assert (not ours.errors(value)) == reference.is_valid(value), value
            checked += 1
    assert checked > 600


def test_demo_items_are_valid():
    schema = ItemSchema.load(DEMO / "item.schema.json")
    assert [schema.errors(item) for item in ITEMS] == [[]] * len(ITEMS)
    assert "classification" in schema.property_names


@pytest.mark.parametrize("value", ["abc\n", "abc\n\n"])
def test_dollar_matches_only_at_the_true_end(value):
    # Python's own "$" would accept the first of these.
    assert ItemSchema({"pattern": "^abc$"}).errors(value)


@pytest.mark.parametrize(
    ("pattern", "accepted", "refused"),
    [
        (r"^a\$$", "a$", "a$\n"),  # an escaped dollar is a literal
        (r"^[$]+$", "$$", "a"),  # so is one inside a class
        (r"^[]$]+$", "]$", "a"),  # a leading "]" does not close the class
        (r"^[^]$]+$", "ab", "a$"),
        (r"a$|^b$", "xa", "a\n"),
        (r"^\d+$", "12", "١٢"),  # ASCII digits only
    ],
)
def test_pattern_dialect(pattern, accepted, refused):
    schema = ItemSchema({"pattern": pattern})
    assert schema.errors(accepted) == []
    assert schema.errors(refused)


def test_messages_say_where_without_repeating_values():
    item = {**ITEMS[0], "lot_number": "SENSITIVE", "immediate_actions": ["ok", 5], "SECRET": 1}
    del item["product"]
    messages = ItemSchema(DEMO_SCHEMA).errors(item)
    joined = " | ".join(messages)
    assert "payload: missing required property 'product'" in joined
    assert "payload.lot_number: must match the pattern" in joined
    assert "payload.immediate_actions[1]: must be of type string, not integer" in joined
    assert "payload: unexpected property 'SECRET'" in joined
    assert "SENSITIVE" not in joined


def test_a_non_json_value_is_reported():
    assert "not date" in ItemSchema({"type": "string"}).errors(__import__("datetime").date.min)[0]


def test_property_errors():
    schema = ItemSchema(DEMO_SCHEMA)
    assert schema.property_errors("classification", "major", "correct_value") == []
    assert "correct_value" in schema.property_errors("classification", "huge", "correct_value")[0]


def test_a_boolean_schema_names_no_properties():
    assert ItemSchema(True).property_names == frozenset()


@pytest.mark.parametrize(
    "keyword",
    ["oneOf", "anyOf", "allOf", "not", "$ref", "$defs", "format", "const", "uniqueItems", "tpye"],
)
def test_unsupported_keywords_are_refused_at_any_depth(keyword):
    with pytest.raises(SchemaError, match="unsupported keyword"):
        ItemSchema({keyword: 1})
    with pytest.raises(SchemaError, match=r"#/properties/a/items"):
        ItemSchema({"properties": {"a": {"items": {keyword: 1}}}})


@pytest.mark.parametrize(
    "schema",
    [
        None,
        [],
        {"type": "text"},
        {"type": []},
        {"properties": []},
        {"properties": {1: {}}},
        {"properties": {"a": 1}},
        {"items": [{}]},
        {"additionalProperties": "no"},
        {"required": "a"},
        {"required": [1]},
        {"enum": []},
        {"enum": "a"},
        {"pattern": 1},
        {"pattern": "("},
        {"minimum": "1"},
        {"maximum": True},
        {"minimum": float("nan")},
        {"minLength": -1},
        {"minItems": 1.5},
        {"maxItems": True},
    ],
)
def test_malformed_schemas_are_refused(schema):
    with pytest.raises(SchemaError):
        ItemSchema(schema)


@pytest.mark.parametrize("text", ['{"type": "string", "type": "object"}', "{", '{"minimum": NaN}'])
def test_bad_schema_files_are_refused(tmp_path, text):
    path = tmp_path / "s.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SchemaError):
        ItemSchema.load(path)


def test_missing_schema_file(tmp_path):
    with pytest.raises(SchemaError, match="cannot read"):
        ItemSchema.load(tmp_path / "absent.json")


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (1, 1.0, True),
        (True, 1, False),
        (0, False, False),
        ([1, True], [1.0, True], True),
        ([1], [1, 2], False),
        ({"a": 1}, {"a": 1.0}, True),
        ({"a": 1}, {"b": 1}, False),
        ("1", 1, False),
        (None, None, True),
    ],
)
def test_json_equality(left, right, expected):
    assert json_equal(left, right) is expected
