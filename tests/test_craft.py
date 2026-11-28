"""Drafting planted errors from real agent output (ADR-009).

Every "real" detail here is invented. ``FOUND`` and ``MISSED`` record what the search for them
does and does not find: the documentation states the same limits.
"""

import copy
import json
import shutil
import socket
import sqlite3
import unicodedata
from datetime import date

import pytest
import yaml
from deploy import DEMO, EXAMPLES, ITEMS, QUEUE, Deployment, submit_until_pair

from reviewproof import craft
from reviewproof.cli.main import main
from reviewproof.config import load_config
from reviewproof.planted import PlantedImportError
from reviewproof.strictload import load_file

PROFILE = {
    "queue": QUEUE,
    "properties": {
        "deviation_id": {"handling": "replace", "pattern": "DEV-2031-{4 digits}"},
        "occurred_on": "shift_date",
        "product": "keep",
        "lot_number": "replace",
        "process_step": "keep",
        "description": "review",
        "classification": "keep",
        "classification_rationale": "review",
        "root_cause": "review",
        "immediate_actions": "review",
        "capa_required": "keep",
    },
    "blocked_terms": ["Marta Quillfeather", "Brindlewick", "Ose"],
}


def export_items():
    first = {
        **copy.deepcopy(ITEMS[2]),
        "deviation_id": "DEV-2026-0457",
        "occurred_on": "2026-03-04",
        "lot_number": "LOT-C77881",
        "description": "Batch LOT-C77881 was compressed on 2026-03-04. A punch tip was found "
        "missing at the end of the run.",
    }
    second = {
        **copy.deepcopy(ITEMS[0]),
        "deviation_id": "DEV-2026-0812",
        "lot_number": "LOT-C66554",
    }
    return [first, second]


class Draft:
    """A folder with the settings, a profile and an export, and a draft of item 0."""

    def __init__(self, folder, profile=None, items=None):
        self.folder = folder
        for name in ("reviewproof.yaml", "item.schema.json"):
            shutil.copy(DEMO / name, folder / name)
        self.config = load_config(folder / "reviewproof.yaml")
        self.profile_path = folder / "handling.yaml"
        self.profile_path.write_text(
            yaml.safe_dump(profile or PROFILE, allow_unicode=True), encoding="utf-8"
        )
        self.export_path = folder / "export.json"
        self.export_path.write_text(json.dumps(items or export_items()), encoding="utf-8")

    @property
    def profile(self):
        return craft.load_profile(self.profile_path, self.config)

    @property
    def export(self):
        return craft.load_export(self.export_path, self.config, self.profile)

    def start(self, **changes):
        arguments = {
            "planted_error_id": "PE-REAL-1",
            "author": "author-1",
            "new_values": {"lot_number": "LOT-C12345"},
            "shift_days": 500,
            **changes,
        }
        return craft.start(self.config, self.profile, self.export, 0, **arguments)

    def defective(self):
        """A working file with one defect: the classification, with its rationale reworded."""
        working = self.start()
        working["defect_class"] = "wrong_classification"
        working["defect_field"] = "classification"
        working["expected_decision"] = "edit:classification"
        working["payload"]["classification"] = "major"
        working["payload"]["classification_rationale"] = "Only some units could be affected."
        return working

    def check(self, working):
        return craft.check(self.config, self.profile, self.export, working)

    def finish(self, working, **changes):
        arguments = {
            "source_confirmed_by": "qa-2",
            "checked_for_real_data_by": "qa-3",
            "acknowledged": [w.code for w in self.check(working).warnings],
            **changes,
        }
        return craft.finish(self.config, self.profile, self.export, working, **arguments)


@pytest.fixture
def draft(tmp_path):
    return Draft(tmp_path)


# ----------------------------------------------------------------- the profile and export


@pytest.mark.req("REQ-059")
@pytest.mark.parametrize("left_out", sorted(PROFILE["properties"]))
def test_a_property_missing_from_the_profile_stops_everything(tmp_path, left_out):
    profile = copy.deepcopy(PROFILE)
    del profile["properties"][left_out]
    draft = Draft(tmp_path, profile)
    with pytest.raises(craft.CraftError, match=f"does not say how to handle: {left_out}"):
        _ = draft.profile


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: p["properties"].update(extra="keep"), "does not have"),
        (lambda p: p["properties"].update(product="copy"), "handling must be one of"),
        (
            lambda p: p["properties"].update(product={"handling": "keep", "pattern": "{2 digits}"}),
            "only a replaced value",
        ),
        (lambda p: p["properties"].update(product={"handling": "replace"}), "handling and pattern"),
        (
            lambda p: p["properties"].update(lot_number={"handling": "replace", "pattern": "LOT"}),
            "a pattern is text",
        ),
        (
            lambda p: p["properties"].update(
                lot_number={"handling": "replace", "pattern": "{3 numbers}{2 digits}"}
            ),
            "a pattern is text",
        ),
        (lambda p: p.update(blocked_terms=None), "blocked_terms"),
        (lambda p: p.update(blocked_terms=["x"]), "blocked_terms"),
        (lambda p: p.update(blocked_terms=["- _"]), "blocked_terms"),
        (lambda p: p.pop("blocked_terms"), "exactly queue, properties and blocked_terms"),
        (lambda p: p.update(queue="nowhere"), "unknown queue"),
        (lambda p: p.update(properties=[]), "properties must list"),
    ],
)
def test_a_wrong_profile_is_refused(tmp_path, change, message):
    profile = copy.deepcopy(PROFILE)
    change(profile)
    draft = Draft(tmp_path, profile)
    with pytest.raises(craft.CraftError, match=message):
        _ = draft.profile


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda items: items[1].update(patient="Zebedee"), "export item 1: has a property"),
        (lambda items: items[1].pop("root_cause"), "export item 1: does not fit"),
        (lambda items: items[0].update(lot_number="Zebedee"), "'lot_number' does not fit"),
        (lambda items: items.append("Zebedee"), "export item 2 is not an item"),
        (lambda items: items.clear(), "must be a list of items"),
    ],
)
def test_a_wrong_export_is_refused_without_repeating_it(draft, change, message):
    items = export_items()
    change(items)
    draft.export_path.write_text(json.dumps(items), encoding="utf-8")
    with pytest.raises(craft.CraftError, match=message) as refused:
        _ = draft.export
    assert "Zebedee" not in str(refused.value)


def test_a_date_that_does_not_exist_is_refused(draft):
    items = export_items()
    items[1]["occurred_on"] = "2026-02-30"
    draft.export_path.write_text(json.dumps(items), encoding="utf-8")
    with pytest.raises(craft.CraftError, match="written YYYY-MM-DD"):
        _ = draft.export


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("export.json", '[{"Zebedee": 1, "Zebedee": 2}]'),
        ("export.json", '[{"Zebedee": }]'),
        ("handling.yaml", "Zebedee: 1\nZebedee: 2\n"),
        ("handling.yaml", "a: [Zebedee\n"),
        ("export.yaml", "- Zebedee\n"),
    ],
)
def test_a_file_that_cannot_be_read_is_not_quoted(draft, name, content):
    (draft.folder / name).write_text(content, encoding="utf-8")
    with pytest.raises(craft.CraftError) as refused:
        if name == "handling.yaml":
            _ = draft.profile
        else:
            craft.load_export(draft.folder / name, draft.config, draft.profile)
    assert "Zebedee" not in str(refused.value)
    assert refused.value.__cause__ is None


# ----------------------------------------------------------------- start


@pytest.mark.req("REQ-059")
def test_start_replaces_identifiers_and_shifts_dates_everywhere(draft):
    working = draft.start()
    payload = working["payload"]
    assert payload["lot_number"] == "LOT-C12345"
    assert payload["deviation_id"].startswith("DEV-2031-")
    assert payload["occurred_on"] == "2027-07-17"
    assert payload["description"].startswith("Batch LOT-C12345 was compressed on 2027-07-17.")
    assert working["defect_field"] is None
    assert list(payload) == list(PROFILE["properties"])
    assert draft.export.items[0]["lot_number"] == "LOT-C77881"  # the original is untouched


def test_an_identifier_written_differently_is_replaced_too(tmp_path):
    items = export_items()
    items[0]["root_cause"] = "Label lot c77881 was wrong; so was lot-C 77881, and batch C77881."
    draft = Draft(tmp_path, items=items)
    working = draft.start()
    # A part of an identifier is not found: see MISSED.
    assert working["payload"]["root_cause"] == (
        "Label LOT-C12345 was wrong; so was LOT-C12345, and batch C77881."
    )


def test_a_longer_identifier_is_replaced_before_one_inside_it(tmp_path):
    items = export_items()
    items[0]["description"] = "Record DEV-2026-0457-A refers to DEV-2026-0457."
    profile = copy.deepcopy(PROFILE)
    profile["properties"]["product"] = "replace"
    items[0]["product"] = "DEV-2026-0457-A"
    draft = Draft(tmp_path, profile, items)
    working = draft.start(new_values={"lot_number": "LOT-C12345", "product": "NEW-LONG"})
    assert working["payload"]["description"] == (
        f"Record NEW-LONG refers to {working['payload']['deviation_id']}."
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"new_values": {}}, "give a new value for 'lot_number'"),
        ({"new_values": {"lot_number": "LOT-C66554"}}, "holds an identifier from the export"),
        ({"new_values": {"lot_number": "bad"}}, "does not fit the item format"),
        ({"new_values": {"lot_number": "LOT-C12345", "product": "x"}}, "only for the replaced"),
        ({"shift_days": None}, "--shift-days"),
        ({"shift_days": 0}, "not 0"),
        ({"shift_days": 10**9}, "out of range"),
        ({"planted_error_id": "not allowed"}, "the ID must be"),
        ({"author": "  "}, "author"),
    ],
)
def test_start_refuses(draft, changes, message):
    with pytest.raises(craft.CraftError, match=message):
        draft.start(**changes)


def test_start_refuses_an_item_number_outside_the_export(draft):
    with pytest.raises(craft.CraftError, match="from 0 to 1"):
        craft.start(draft.config, draft.profile, draft.export, 2, planted_error_id="PE", author="a")


def test_a_new_value_may_not_hold_a_blocked_term(tmp_path):
    profile = copy.deepcopy(PROFILE)
    profile["properties"]["root_cause"] = "replace"
    draft = Draft(tmp_path, profile)
    values = {"lot_number": "LOT-C12345", "root_cause": "Told to brindlewick"}
    with pytest.raises(craft.CraftError, match="holds a blocked term"):
        draft.start(new_values=values)


def test_a_date_may_not_be_shifted_onto_another_original(tmp_path):
    profile = copy.deepcopy(PROFILE)
    profile["properties"]["root_cause"] = "shift_date"
    items = export_items()
    items[0]["root_cause"] = "2026-03-14"
    items[1]["root_cause"] = "2026-01-01"
    draft = Draft(tmp_path, profile, items)
    with pytest.raises(craft.CraftError, match="another original date"):
        draft.start(shift_days=10)


def test_a_pattern_that_cannot_fit_is_refused(tmp_path):
    profile = copy.deepcopy(PROFILE)
    profile["properties"]["deviation_id"] = {"handling": "replace", "pattern": "X-{2 digits}"}
    draft = Draft(tmp_path, profile)
    with pytest.raises(craft.CraftError, match="made no usable value"):
        draft.start()


def numbered(tmp_path, profile=None):
    """A queue whose items carry a numeric subject ID."""
    draft = Draft(tmp_path, profile or {**PROFILE, "properties": {**PROFILE["properties"]}})
    schema_path = tmp_path / "item.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["subject_number"] = {"type": "integer", "minimum": 1}
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    items = export_items()
    items[0]["subject_number"], items[1]["subject_number"] = 55012, 61177
    draft.export_path.write_text(json.dumps(items), encoding="utf-8")
    written = copy.deepcopy(profile or PROFILE)
    written["properties"]["subject_number"] = "replace"
    draft.profile_path.write_text(yaml.safe_dump(written), encoding="utf-8")
    return draft


def test_a_numeric_identifier_is_replaced_and_searched(tmp_path):
    draft = numbered(tmp_path)
    working = draft.start(new_values={"lot_number": "LOT-C12345", "subject_number": "90001"})
    assert working["payload"]["subject_number"] == 90001
    working.update(
        defect_class="wrong_classification",
        defect_field="classification",
        expected_decision="edit:classification",
    )
    working["payload"]["classification"] = "major"
    working["payload"]["root_cause"] = "Subject 61177 was affected."
    assert draft.check(working).blocks == (
        "a value of 'subject_number' from export item 1 appears in payload.root_cause",
    )


def test_no_dates_to_shift_needs_no_number_of_days(tmp_path):
    profile = copy.deepcopy(PROFILE)
    profile["properties"]["occurred_on"] = "keep"
    working = Draft(tmp_path, profile).start(shift_days=None)
    assert working["shift_days"] is None
    assert working["payload"]["occurred_on"] == "2026-03-04"


def test_a_value_that_is_not_text_or_a_number_cannot_be_replaced(tmp_path):
    profile = copy.deepcopy(PROFILE)
    profile["properties"]["capa_required"] = "replace"
    draft = Draft(tmp_path, profile)
    with pytest.raises(craft.CraftError, match="must be text or a number"):
        _ = draft.export


def test_an_item_format_that_cannot_be_used_is_reported(draft):
    (draft.folder / "item.schema.json").write_text("not a format", encoding="utf-8")
    with pytest.raises(craft.CraftError, match="item format cannot be used"):
        _ = draft.profile


@pytest.mark.parametrize(
    ("content", "message"),
    [(None, "cannot be read"), (b"\xff\xfe\x00", "cannot be read"), (b"[NaN]", "not valid")],
)
def test_an_unreadable_file_is_reported(draft, content, message):
    path = draft.folder / "other.json"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(craft.CraftError, match=message):
        craft.load_export(path, draft.config, draft.profile)


def test_matching_covers_whole_characters_and_nested_values():
    assert craft.find("- _", "a - _ b") == []
    accented = "Jose\u0301 ran it"
    assert [accented[a:b] for a, b in craft.find("Jose", accented)] == ["Jose\u0301"]
    nested = {"a": ["LOT-C66554", {"b": "x"}], "n": 3}
    changed = craft._each_string(nested, str.lower)
    assert changed == {"a": ["lot-c66554", {"b": "x"}], "n": 3}
    assert list(craft._strings(nested)) == ["LOT-C66554", "x"]
    assert list(craft._texts(nested)) == ["a", "LOT-C66554", "b", "x", "n", "3"]


def test_a_value_yaml_turned_into_a_date_is_refused_not_crashed_on(draft):
    working = craft.add_defect(
        draft.config,
        draft.profile,
        draft.export,
        draft.start(),
        "classification-flip",
        "classification",
        to="minor",
    )
    working["payload"]["occurred_on"] = date(2027, 7, 17)
    report = draft.check(working)
    assert "payload 'occurred_on' does not fit the item format" in report.blocks
    assert any("no explanation was reworded" in w.message for w in report.warnings)


# ----------------------------------------------------------------- what must be found

LOT = "LOT-C66554"  # from export item 1, not from the item being drafted
NAME = "Marta Quillfeather"

#: Each must stop the draft: (spelling, what the message says it is).
FOUND = {
    "exact": (LOT, "'lot_number' from export item 1"),
    "lower case": ("lot-c66554", "'lot_number' from export item 1"),
    "spaces for hyphens": ("LOT C66554", "'lot_number' from export item 1"),
    "no separators": ("LOTC66554", "'lot_number' from export item 1"),
    "underscores": ("lot_c66554", "'lot_number' from export item 1"),
    "non-breaking hyphen": ("LOT\u2011C66554", "'lot_number' from export item 1"),
    "minus sign": ("LOT\u2212C66554", "'lot_number' from export item 1"),
    "full-width characters": (
        "\uff2c\uff2f\uff34\uff0d\uff23\uff16\uff16\uff15\uff15\uff14",
        "'lot_number' from export item 1",
    ),
    "zero-width space inside": ("LOT-C66\u200b554", "'lot_number' from export item 1"),
    "broken over lines": ("LOT-\nC66554", "'lot_number' from export item 1"),
    "inside a longer word": ("xxLOT-C66554yy", "'lot_number' from export item 1"),
    "this item's own": ("DEV-2026-0457", "'deviation_id' from export item 0"),
    "blocked term": (NAME, "blocked term number 1"),
    "blocked term, other case": ("MARTA quillfeather", "blocked term number 1"),
    "blocked term, joined": ("MartaQuillfeather", "blocked term number 1"),
    "blocked term, accent added": ("M\u00e1rta Quillfeather", "blocked term number 1"),
    "blocked term, separate accent": (
        unicodedata.normalize("NFD", "M\u00e1rta Quillfeather"),
        "blocked term number 1",
    ),
    "short blocked term as a word": ("seen by Ose today", "blocked term number 3"),
    "short term with an invisible character": ("seen by O\u200bse today", "blocked term number 3"),
}

#: Not found. These are limits of the tool: only a person reading the item can catch them.
MISSED = {
    "part of an identifier": "batch C66554",
    "dots for hyphens": "LOT.C66554",
    "slash for a hyphen": "LOT/C66554",
    "two digits swapped": "LOT-C66545",
    "surname alone": "Quillfeather",
    "initial and surname": "M. Quillfeather",
    "misspelled name": "Marta Quilfeather",
    "a name nobody listed": "Dr. Ptolemy Vance",
    "look-alike letter from another alphabet": "LOT-\u042166554",
    "short term joined to another word": "Oseville",
}

LOCATIONS = ("description", "immediate_actions", "notes", "correct_value")


def put(working, location, text):
    if location == "immediate_actions":
        working["payload"][location][1] = f"Called about {text} at once"
    elif location == "notes":
        working["notes"] = f"Note: {text}."
    elif location == "correct_value":
        # Not the defect field's real correct value, but it would be imported all the same.
        working["correct_value"] = text
    else:
        working["payload"][location] = f"The record {text} was open."


@pytest.mark.req("REQ-060")
@pytest.mark.parametrize("location", LOCATIONS)
@pytest.mark.parametrize("spelling", FOUND)
def test_a_listed_detail_is_found_wherever_it_is_put(draft, spelling, location):
    text, expected = FOUND[spelling]
    working = draft.defective()
    put(working, location, text)
    report = draft.check(working)
    where = location if location in ("notes", "correct_value") else f"payload.{location}"
    assert any(expected in block and block.endswith(where) for block in report.blocks), (
        report.blocks
    )
    assert report.data is None
    assert not any(text in block for block in report.blocks)
    with pytest.raises(craft.CraftError) as refused:
        draft.finish(working)
    assert text not in str(refused.value)


@pytest.mark.parametrize("spelling", MISSED)
def test_what_the_search_misses(draft, spelling):
    working = draft.defective()
    put(working, "description", MISSED[spelling])
    assert draft.check(working).blocks == ()


def test_an_identifier_split_between_two_properties_is_missed(draft):
    working = draft.defective()
    working["payload"]["description"] = "Reference LOT-C66"
    working["payload"]["root_cause"] = "554 was open."
    assert draft.check(working).blocks == ()


@pytest.mark.req("REQ-060")
@pytest.mark.parametrize(
    ("key", "value", "where"),
    [
        ("planted_error_id", "PE-LOT-C66554", "planted_error_id"),
        ("author", "marta.quillfeather@x", None),
        ("author", "Brindlewick QA", "author"),
    ],
)
def test_the_id_and_author_are_searched(draft, key, value, where):
    working = {**draft.defective(), key: value}
    blocks = draft.check(working).blocks
    if where is None:
        assert blocks == ()  # a dot is not a separator the search ignores
    else:
        assert any(block.endswith(where) for block in blocks), blocks


def test_a_number_in_the_payload_is_searched(tmp_path):
    profile = copy.deepcopy(PROFILE)
    profile["blocked_terms"] = ["884422"]
    draft = Draft(tmp_path, profile)
    working = draft.defective()
    working["payload"]["capa_required"] = 884422
    assert any("blocked term number 1" in b for b in draft.check(working).blocks)


def test_an_unlisted_property_is_not_named(draft):
    working = draft.defective()
    working["payload"]["Marta Quillfeather"] = "x"
    report = draft.check(working)
    assert "payload has a property the item format does not have" in report.blocks
    assert "blocked term number 1 appears in an unlisted property of payload" in report.blocks
    assert "Quillfeather" not in repr(report)


@pytest.mark.req("REQ-060")
@pytest.mark.parametrize("location", ["description", "notes"])
def test_an_unshifted_date_is_found(draft, location):
    working = draft.defective()
    put(working, location, "2026-03-04")
    assert any(
        block.startswith("an unshifted date from 'occurred_on'")
        for block in draft.check(working).blocks
    )


# ----------------------------------------------------------------- one defect only


@pytest.mark.req("REQ-061")
def test_a_good_draft_has_nothing_stopping_it(draft):
    report = draft.check(draft.defective())
    assert report.blocks == ()
    assert report.changed == ("classification", "classification_rationale")
    assert report.data["correct_value"] == "critical"


@pytest.mark.req("REQ-061")
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("product", "Mockaril 5 mg tablets"),
        ("lot_number", "LOT-C54321"),
        ("occurred_on", "2027-07-18"),
        ("capa_required", False),
        ("deviation_id", "DEV-2031-0001"),
    ],
)
def test_a_second_structured_change_is_refused(draft, name, value):
    working = draft.defective()
    working["payload"][name] = value
    assert any(
        block.startswith(f"'{name}' is handled as") and "second defect" in block
        for block in draft.check(working).blocks
    )


@pytest.mark.req("REQ-061")
@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda w: w["payload"].update(classification="critical"), "so there is no defect"),
        (lambda w: w.update(defect_class=None), "no defect yet"),
        (lambda w: w.update(expected_decision=None), "no defect yet"),
        (lambda w: w.update(defect_field="Marta Quillfeather"), "not a property"),
        (lambda w: w["payload"].pop("root_cause"), "does not fit the item format"),
        (lambda w: w["payload"].update(classification="Zebedee"), "'classification' does not fit"),
        (lambda w: w["payload"].update(root_cause="See rp-reserved-1."), "reserved target"),
        (lambda w: w.update(expected_decision="approve"), "expected_decision"),
        (lambda w: w.update(defect_class="typo"), "defect_class"),
        (lambda w: w.update(correct_value="Zebedee"), "correct_value"),
        (lambda w: w.update(purpose="practice"), "purpose"),
    ],
)
def test_what_could_not_be_imported_is_refused(draft, change, message):
    working = draft.defective()
    change(working)
    report = draft.check(working)
    assert any(message in block for block in report.blocks), report.blocks
    assert "Zebedee" not in repr(report) and "Quillfeather" not in repr(report)


def test_a_nested_property_name_is_not_repeated(draft, monkeypatch):
    def refuse(config, data):
        raise PlantedImportError("correct_value: unexpected property 'Zebedee'")

    monkeypatch.setattr(craft.PlantedLibrary, "check_data", refuse)
    report = draft.check(draft.defective())
    assert report.blocks == ("the correct value or another entry does not fit the item format",)


@pytest.mark.parametrize(
    ("mutator", "field", "arguments", "defect_class"),
    [
        ("value-swap", "product", {"new_value": "Mockaril 5 mg tablets"}, "wrong_value"),
        ("omission", "immediate_actions", {"index": 0}, "omission"),
        ("classification-flip", "classification", {"to": "minor"}, "wrong_classification"),
    ],
)
def test_the_drafting_helpers_add_one_defect(draft, mutator, field, arguments, defect_class):
    working = craft.add_defect(
        draft.config, draft.profile, draft.export, draft.start(), mutator, field, **arguments
    )
    assert working["defect_class"] == defect_class
    report = draft.check(working)
    assert report.blocks == () and report.changed == (field,)
    with pytest.raises(craft.CraftError, match="already has a defect"):
        craft.add_defect(
            draft.config, draft.profile, draft.export, working, mutator, field, **arguments
        )


@pytest.mark.parametrize(
    ("mutator", "field", "change", "message"),
    [
        ("value-swap", "product", None, "same as the current"),
        ("sabotage", "product", None, "mutator must be one of"),
        ("omission", "product", lambda w: w["payload"].pop("root_cause"), "does not fit"),
    ],
)
def test_the_drafting_helpers_refuse(draft, mutator, field, change, message):
    working = draft.start()
    if change:
        change(working)
    with pytest.raises(craft.CraftError, match=message):
        craft.add_defect(
            draft.config,
            draft.profile,
            draft.export,
            working,
            mutator,
            field,
            new_value=working["payload"].get(field),
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda w: w.update(queue="other"), "different queues"),
        (lambda w: w.update(export_item=9), "does not have"),
        (lambda w: w.update(new_values=[]), "new_values must hold exactly"),
        (lambda w: w["new_values"].pop("deviation_id"), "new_values must hold exactly"),
        (lambda w: w.update(payload=[]), "the payload must be"),
    ],
)
def test_a_working_file_that_does_not_match_is_refused(draft, change, message):
    working = draft.defective()
    change(working)
    with pytest.raises(craft.CraftError, match=message):
        draft.check(working)


def test_a_changed_export_is_refused(draft):
    working = draft.defective()
    items = export_items()
    items.reverse()
    draft.export_path.write_text(json.dumps(items), encoding="utf-8")
    with pytest.raises(craft.CraftError, match="not the export"):
        draft.check(working)


@pytest.mark.req("REQ-061")
def test_another_correct_value_does_not_make_an_unchanged_field_a_defect(draft):
    # The import rules alone would take this: the two values differ. The item is still the
    # original, so it holds no defect.
    working = draft.defective()
    working["payload"]["classification"] = "critical"
    working["correct_value"] = "minor"
    assert draft.check(working).blocks == (
        "'classification' is unchanged from the original, so there is no defect",
    )


# ----------------------------------------------------------------- warnings and finishing


def warnings(draft, working):
    return [warning.message for warning in draft.check(working).warnings]


@pytest.mark.parametrize(
    ("text", "what"),
    [
        ("Mail zed@example.invalid today.", "an email address"),
        ("Seen on 14/03/2026 here.", "a date"),
        ("Seen on 3 April here.", "a date"),
        ("Seen on April 3 here.", "a date"),
        ("Seen on 2031-01-02 here.", "a date"),
        ("Ring +44 20 7946 0958 now.", "a telephone number"),
        ("Order 55012 was open.", "a long number"),
    ],
)
def test_suspicious_text_is_warned_about(draft, text, what):
    working = draft.defective()
    working["payload"]["description"] = text
    assert f"text that looks like {what} in payload.description" in warnings(draft, working)
    assert not any(text in message for message in warnings(draft, working))


@pytest.mark.parametrize(
    ("name", "typed", "what"),
    [
        # A real lot number that no exported item mentions: only a warning can catch it.
        ("lot_number", "LOT-C99817", "a long number"),
        ("occurred_on", "2025-11-02", "a date"),
    ],
)
def test_a_typed_wrong_value_is_warned_about_whatever_its_handling(draft, name, typed, what):
    working = craft.add_defect(
        draft.config,
        draft.profile,
        draft.export,
        draft.start(),
        "value-swap",
        name,
        new_value=typed,
    )
    assert draft.check(working).blocks == ()
    assert f"text that looks like {what} in payload.{name}" in warnings(draft, working)
    assert not any("correct_value" in message for message in warnings(draft, working))


def test_a_number_is_warned_about_too(tmp_path):
    draft = numbered(tmp_path)
    working = draft.start(new_values={"lot_number": "LOT-C12345", "subject_number": 90001})
    working = craft.add_defect(
        draft.config,
        draft.profile,
        draft.export,
        working,
        "value-swap",
        "subject_number",
        new_value=73559,
    )
    assert "text that looks like a long number in payload.subject_number" in warnings(
        draft, working
    )


def test_the_new_identifiers_and_shifted_dates_raise_nothing(draft):
    working = draft.defective()
    working["payload"]["description"] = f"LOT-C12345 {working['payload']['occurred_on']}"
    assert not any("looks like" in message for message in warnings(draft, working))


def test_a_short_new_value_does_not_hide_a_warning(tmp_path):
    draft = numbered(tmp_path)
    working = draft.start(new_values={"lot_number": "LOT-C12345", "subject_number": 5})
    working["payload"]["description"] = "Order 75512 was open."
    assert "text that looks like a long number in payload.description" in warnings(draft, working)


def test_copied_sentences_are_warned_about(draft):
    messages = warnings(draft, draft.defective())
    assert "2 sentence(s) copied unchanged from the original in payload.description" in messages
    assert not any("classification_rationale" in message for message in messages)


def test_an_unreworded_explanation_is_warned_about(draft):
    working = craft.add_defect(
        draft.config,
        draft.profile,
        draft.export,
        draft.start(),
        "classification-flip",
        "classification",
        to="minor",
    )
    assert "the classification changed and no explanation was reworded" in warnings(draft, working)


@pytest.mark.req("REQ-062")
def test_every_warning_must_be_acknowledged(draft):
    working = draft.defective()
    codes = [w.code for w in draft.check(working).warnings]
    assert len(codes) > 1
    for kept in (codes[:-1], []):
        with pytest.raises(craft.CraftError, match="acknowledge it"):
            draft.finish(working, acknowledged=kept)
    assert draft.finish(working, acknowledged=codes)["provenance"]


@pytest.mark.req("REQ-062")
def test_an_acknowledgment_stops_counting_when_the_text_changes(draft):
    working = draft.defective()
    codes = [w.code for w in draft.check(working).warnings]
    working["payload"]["description"] += " One more thing was seen later."
    with pytest.raises(craft.CraftError, match="matches no warning now"):
        draft.finish(working, acknowledged=codes)
    still_good = [w.code for w in draft.check(working).warnings if w.code in codes]
    assert 0 < len(still_good) < len(codes)
    with pytest.raises(craft.CraftError, match="acknowledge it"):
        draft.finish(working, acknowledged=still_good)


@pytest.mark.req("REQ-062")
@pytest.mark.parametrize("who", ["source_confirmed_by", "checked_for_real_data_by"])
@pytest.mark.parametrize(
    ("name", "message"),
    [
        # "must be": the drafting tool's own refusal, not import's.
        ("author-1", "must be someone other than the author"),
        (" AUTHOR-1 ", "must be someone other than the author"),
        ("", "must name who did it"),
        ("Marta Quillfeather", "pseudonymous"),
    ],
)
def test_both_checks_need_someone_other_than_the_author(draft, who, name, message):
    with pytest.raises(craft.CraftError, match=message):
        draft.finish(draft.defective(), **{who: name})


def test_one_other_person_may_do_both_checks(draft):
    data = draft.finish(
        draft.defective(), source_confirmed_by="qa-2", checked_for_real_data_by="qa-2"
    )
    assert data["provenance"]["checked_for_real_data_by"] == "qa-2"


def test_finish_refuses_what_import_would(draft, monkeypatch):
    working = draft.defective()
    codes = [w.code for w in draft.check(working).warnings]
    original = craft.PlantedLibrary.check_data.__func__

    def refuse(config, data):
        if "provenance" in data:
            raise PlantedImportError("refused at the end")
        original(craft.PlantedLibrary, config, data)

    monkeypatch.setattr(craft.PlantedLibrary, "check_data", refuse)
    with pytest.raises(craft.CraftError, match="refused at the end"):
        draft.finish(working, acknowledged=codes)


# ----------------------------------------------------------------- the finished planted error


def finished_file(draft):
    path = draft.folder / "PE-REAL-1.yaml"
    craft.write_finished(draft.finish(draft.defective()), path)
    return path


@pytest.mark.req("REQ-064")
def test_the_finished_file_keeps_nothing_that_identifies_the_original(draft):
    text = finished_file(draft).read_text(encoding="utf-8")
    for original in ("DEV-2026-0457", "LOT-C77881", "2026-03-04", draft.export.sha256):
        assert original not in text
    data = load_file(draft.folder / "PE-REAL-1.yaml")
    assert set(data["provenance"]) == {
        "origin",
        "source_confirmed_by",
        "checked_for_real_data_by",
        "tool_version",
        "drafted_at",
    }
    assert "export_item" not in data and "export_sha256" not in data


@pytest.mark.req("REQ-064")
def test_provenance_is_stored_fixed_and_committed_to_by_the_log(tmp_path):
    (tmp_path / "files").mkdir()
    draft = Draft(tmp_path / "files")
    (tmp_path / "live").mkdir()
    deployment = Deployment(tmp_path / "live", 0.5)
    ov = deployment.open()
    imported = ov.planted.import_file(finished_file(draft))
    assert imported.provenance["origin"] == "real_output"
    assert "provenance" not in repr(imported)
    ov.planted.approve("PE-REAL-1", "qa-4")
    ov.planted.approve("PE-REAL-1", "qa-5")
    submit_until_pair(ov)  # the planted error goes out with a real item
    assert deployment.verify().ok
    deployment.close()
    with sqlite3.connect(deployment.planted_db) as db:
        with pytest.raises(sqlite3.IntegrityError, match="cannot be changed"):
            db.execute("UPDATE planted_errors SET provenance = NULL")
        # Even with the trigger gone, the log shows a change.
        db.execute("DROP TRIGGER planted_errors_provenance_fixed")
        db.execute("UPDATE planted_errors SET provenance = replace(provenance, 'qa-3', 'qa-9')")
    db.close()
    result = deployment.verify()
    assert not result.ok and result.registry_errors


@pytest.mark.req("REQ-064")
@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(origin="synthetic"),
        lambda p: p.pop("source_confirmed_by"),
        lambda p: p.update(extra="x"),
        lambda p: p.update(source_confirmed_by="author-1"),
        lambda p: p.update(checked_for_real_data_by="Author-1 "),
        lambda p: p.update(drafted_at=""),
        lambda p: p.update(tool_version=1),
    ],
)
def test_wrong_provenance_is_not_imported(draft, change):
    data = draft.finish(draft.defective())
    change(data["provenance"])
    with pytest.raises(PlantedImportError, match="provenance"):
        craft.PlantedLibrary.check_data(draft.config, data)


@pytest.mark.req("REQ-064")
def test_a_working_file_cannot_be_imported(draft):
    working = draft.defective()
    with pytest.raises(PlantedImportError, match="unknown key.*craft_working_file"):
        craft.PlantedLibrary.check_data(draft.config, working)


def test_load_working_refuses_other_files(draft):
    with pytest.raises(craft.CraftError, match="not a working file"):
        craft.load_working(finished_file(draft))
    path = draft.folder / "work.yaml"
    working = draft.start()
    del working["export_sha256"]
    craft.write_working(working, path)
    with pytest.raises(craft.CraftError, match="lacks: export_sha256"):
        craft.load_working(path)
    path.unlink()
    craft.write_working({**draft.start(), "extra": 1}, path)
    with pytest.raises(craft.CraftError, match="should not have"):
        craft.load_working(path)


@pytest.mark.req("REQ-064")
def test_list_and_approval_say_that_it_came_from_real_output(tmp_path, capsys):
    (tmp_path / "files").mkdir()
    path = finished_file(Draft(tmp_path / "files"))
    (tmp_path / "live").mkdir()
    deployment = Deployment(tmp_path / "live", 0.5)
    where = ["--config", tmp_path / "live" / "reviewproof.yaml"]
    where += ["--planted", deployment.planted_db]

    def plant(action, *arguments):
        assert main([str(a) for a in ["plant", action, *where, *arguments]]) == 0
        return capsys.readouterr().out

    assert "[from real output]" in plant("import", path)
    approval = plant("approve", "PE-REAL-1", "--approver", "qa-4")
    assert "you confirm that you read the whole item and found no real information" in approval
    assert "[from real output]" in plant("list")
    other = plant("import", EXAMPLES[0])
    assert "real output" not in other
    assert "real information" not in plant("approve", "DEMO-OM-1", "--approver", "qa-4")


# ----------------------------------------------------------------- the commands


class Commands:
    def __init__(self, draft):
        self.draft = draft
        self.settings = [
            "--config",
            draft.folder / "reviewproof.yaml",
            "--profile",
            draft.profile_path,
            "--export",
            draft.export_path,
        ]

    def __call__(self, action, *arguments):
        return main([str(a) for a in ["craft", action, *self.settings, *arguments]])

    def start(self, out="work.yaml", extra=()):
        return self(
            "start",
            "--item",
            "0",
            "--id",
            "PE-REAL-1",
            "--author",
            "author-1",
            "--set",
            "lot_number=LOT-C12345",
            "--shift-days",
            "500",
            "--out",
            self.draft.folder / out,
            *extra,
        )


@pytest.fixture
def commands(draft):
    return Commands(draft)


def everything_in(folder):
    return {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}


@pytest.mark.req("REQ-005")
@pytest.mark.req("REQ-063")
def test_the_whole_flow_opens_no_database_and_no_connection(draft, commands, capsys, monkeypatch):
    def forbidden(*arguments, **keywords):
        raise AssertionError("rp craft must not do this")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.chdir(draft.folder)
    folder = draft.folder
    before = everything_in(folder)

    assert commands.start() == 0
    assert "MAY STILL HOLD REAL INFORMATION" in capsys.readouterr().out
    defect = ["--work", folder / "work.yaml", "--mutator", "classification-flip"]
    defect += ["--field", "classification", "--to", "major", "--out", folder / "work2.yaml"]
    assert commands("defect", *defect) == 0
    assert commands("check", "--work", folder / "work2.yaml") == 0
    out = capsys.readouterr().out
    assert "Nothing stops it. This does not show" in out
    acknowledged = []
    for line in out.splitlines():
        if line.startswith("WARNING"):
            acknowledged += ["--acknowledge", line.split()[1]]
    checks = ["--source-confirmed-by", "qa-2", "--checked-for-real-data-by", "qa-3"]
    finish = ["--work", folder / "work2.yaml", *checks, "--out", folder / "final.yaml"]
    assert commands("finish", *finish) == 2
    assert commands("finish", *finish, *acknowledged) == 0
    assert "does not delete them" in capsys.readouterr().out

    after = everything_in(folder)
    assert set(after) - set(before) == {"work.yaml", "work2.yaml", "final.yaml"}
    assert {name: after[name] for name in before} == before


@pytest.mark.req("REQ-063")
def test_nothing_is_overwritten(draft, commands, capsys):
    assert commands.start() == 0
    for target in ("work.yaml", "export.json", "handling.yaml"):
        before = (draft.folder / target).read_bytes()
        assert commands.start(out=target) == 2
        assert "nothing was overwritten" in capsys.readouterr().err
        assert (draft.folder / target).read_bytes() == before


@pytest.mark.req("REQ-063")
def test_no_command_repeats_a_value_from_the_export(draft, commands, capsys):
    items = export_items()
    secrets = ["LOT-C77881", "DEV-2026-0457", "LOT-C66554", "DEV-2026-0812", "2026-03-04"]
    assert commands.start() == 0
    work = draft.folder / "work.yaml"
    commands("check", "--work", work)
    text = work.read_text(encoding="utf-8").replace("LOT-C12345 was", "LOT-C66554 Brindlewick")
    work.unlink()
    work.write_text(text, encoding="utf-8")
    commands("check", "--work", work)
    commands.start(extra=["--set", "deviation_id=DEV-2026-0812"])
    commands.start(extra=["--set", "lot_number"])
    items[1]["Brindlewick"] = "LOT-C66554"
    draft.export_path.write_text(json.dumps(items), encoding="utf-8")
    commands("check", "--work", work)
    draft.export_path.write_text('[{"LOT-C66554": 1, "LOT-C66554": 2}]', encoding="utf-8")
    commands("check", "--work", work)
    captured = capsys.readouterr()
    said = captured.out + captured.err
    assert "STOPS IT" in said and "error:" in said
    for secret in [*secrets, "Brindlewick", "punch tip"]:
        assert secret not in said, secret


@pytest.mark.req("REQ-063")
def test_an_unexpected_failure_shows_only_its_type(commands, capsys, monkeypatch):
    def explode(*arguments, **keywords):
        raise KeyError("LOT-C77881")

    monkeypatch.setattr(craft, "start", explode)
    assert commands.start() == 2
    captured = capsys.readouterr()
    assert "KeyError" in captured.err
    assert "LOT-C77881" not in captured.out + captured.err
    assert "Traceback" not in captured.err


def test_the_value_swap_command(draft, commands, capsys):
    assert commands.start() == 0
    base = ["--work", draft.folder / "work.yaml", "--mutator", "value-swap"]
    assert commands("defect", *base, "--field", "product", "--out", draft.folder / "x.yaml") == 2
    assert "needs --new-value" in capsys.readouterr().err
    flag = [*base, "--field", "capa_required", "--new-value", "false"]
    assert commands("defect", *flag, "--out", draft.folder / "x.yaml") == 0
    assert load_file(draft.folder / "x.yaml")["payload"]["capa_required"] is False
    assert commands("check", "--work", draft.folder / "x.yaml") == 0
    assert "Differs from the original in: capa_required" in capsys.readouterr().out
    word = [*base, "--field", "capa_required", "--new-value", "perhaps"]
    assert commands("defect", *word, "--out", draft.folder / "y.yaml") == 2
    capsys.readouterr()
    assert commands("check", "--work", draft.folder / "work.yaml") == 2
    assert "It cannot be finished yet." in capsys.readouterr().out


# ----------------------------------------------------------------- the example


def test_the_example_profile_works_with_the_demo_items():
    config = load_config(DEMO / "reviewproof.yaml")
    profile = craft.load_profile(DEMO / "handling.yaml", config)
    export = craft.load_export(DEMO / "items.json", config, profile)
    working = craft.start(
        config,
        profile,
        export,
        2,
        planted_error_id="PE-DEMO",
        author="a",
        new_values={"lot_number": "LOT-C39001"},
        shift_days=400,
    )
    working = craft.add_defect(
        config, profile, export, working, "classification-flip", "classification", to="major"
    )
    assert craft.check(config, profile, export, working).blocks == ()


def test_the_guide_shows_the_example_profile():
    guide = (DEMO.parents[1] / "docs" / "guides" / "writing-planted-errors.md").read_text("utf-8")
    shown = guide.split("```yml")[1].split("```")[0]
    assert yaml.safe_load(shown) == load_file(DEMO / "handling.yaml")
