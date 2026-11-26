import copy
import dataclasses
import json
import shutil
from pathlib import Path

import pytest
import yaml
from helpers import triggers_removed

from reviewproof.config import load_config
from reviewproof.models import DefectClass
from reviewproof.planted import (
    ApprovalError,
    PlantedImportError,
    PlantedLibrary,
    Status,
    UnknownPlantedError,
)
from reviewproof.store import StoreError

DEMO = Path(__file__).parents[1] / "examples" / "deviation_triage"
ITEMS = json.loads((DEMO / "items.json").read_text(encoding="utf-8"))
QUEUE = "deviation-triage"


def planted_input(**changes):
    """A good planted error: a major deviation wrongly proposed as minor."""
    payload = {**copy.deepcopy(ITEMS[1]), "deviation_id": "DEV-2026-0190"}
    payload["classification"] = "minor"
    return {
        "planted_error_id": "PE-0001",
        "queue": QUEUE,
        "defect_class": "wrong_classification",
        "defect_field": "classification",
        "expected_decision": "edit:classification",
        "correct_value": "major",
        "author": "author-1",
        "payload": payload,
        **changes,
    }


@pytest.fixture
def folder(tmp_path):
    """A private copy of the demo settings, so that tests can change them."""
    for name in ("reviewproof.yaml", "item.schema.json"):
        shutil.copy(DEMO / name, tmp_path / name)
    return tmp_path


@pytest.fixture
def config(folder):
    return load_config(folder / "reviewproof.yaml")


@pytest.fixture
def library(registry_db, config):
    return PlantedLibrary(registry_db, config)


def with_gxp(library, registry_db, config, gxp):
    queue = dataclasses.replace(config.queues[QUEUE], gxp=gxp)
    return PlantedLibrary(registry_db, dataclasses.replace(config, queues={QUEUE: queue}))


def available_ids(library):
    return [p.planted_error_id for p in library.available(QUEUE)]


# ----------------------------------------------------------------- import


def test_import_stores_an_unapproved_planted_error(library, config):
    planted = library.import_data(planted_input(notes="note"))
    assert planted.planted_error_id == "PE-0001"
    assert planted.queue == QUEUE
    assert planted.defect_class is DefectClass.WRONG_CLASSIFICATION
    assert planted.payload == planted_input()["payload"]
    assert planted.correct_value == "major"
    assert planted.has_correct_value
    assert library.status(planted) is Status.AWAITING_APPROVAL
    assert planted.reserved_target_id.startswith(config.reserved_target_prefix)
    assert available_ids(library) == []
    assert library.all() == library.all(QUEUE) == [planted]
    assert library.all("another") == []


@pytest.mark.parametrize("suffix", [".yaml", ".yml", ".json"])
def test_import_from_a_file(library, tmp_path, suffix):
    path = tmp_path / f"pe{suffix}"
    data = planted_input()
    text = json.dumps(data) if suffix == ".json" else yaml.safe_dump(data, allow_unicode=True)
    path.write_text(text, encoding="utf-8")
    assert library.import_file(path).payload == data["payload"]


def test_a_file_saved_with_a_byte_order_mark_imports(library, tmp_path):
    path = tmp_path / "pe.json"
    path.write_text(json.dumps(planted_input()), encoding="utf-8-sig")
    library.import_file(path)


def test_reserved_target_ids_are_unique(library):
    first = library.import_data(planted_input())
    second_payload = {**planted_input()["payload"], "deviation_id": "DEV-2026-0191"}
    second = library.import_data(planted_input(planted_error_id="PE-2", payload=second_payload))
    assert first.reserved_target_id != second.reserved_target_id


def test_secrets_are_not_in_the_repr(library):
    planted = library.import_data(planted_input(notes="SECRET-NOTE"))
    text = repr(planted)
    assert "DEV-2026-0190" not in text
    assert "SECRET-NOTE" not in text
    assert planted.reserved_target_id not in text


def test_an_omission_may_drop_a_list_entry(library):
    payload = planted_input()["payload"]
    full = list(payload["immediate_actions"])
    payload["immediate_actions"] = full[1:]
    library.import_data(
        planted_input(
            defect_class="omission",
            defect_field="immediate_actions",
            expected_decision="reject",
            correct_value=full,
            payload=payload,
        )
    )


def test_an_omission_may_drop_an_optional_property(library, folder):
    schema = json.loads((folder / "item.schema.json").read_text(encoding="utf-8"))
    schema["required"].remove("root_cause")
    (folder / "item.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    data = planted_input(
        defect_class="omission", defect_field="root_cause", expected_decision="reject"
    )
    del data["correct_value"]
    del data["payload"]["root_cause"]
    assert not library.import_data(data).has_correct_value


def damaged(**changes):
    data = planted_input()
    data["payload"].update(changes)
    return data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ("text", "must be a mapping"),
        (planted_input(approved_by="qa-1"), "unknown key.*approved_by"),
        ({k: v for k, v in planted_input().items() if k != "author"}, "missing key.*author"),
        (planted_input(planted_error_id="has space"), "planted_error_id"),
        (planted_input(planted_error_id=7), "planted_error_id"),
        (planted_input(queue="nope"), "unknown queue.*deviation-triage"),
        (planted_input(queue=["q"]), "unknown queue"),
        (planted_input(defect_class="typo"), "defect_class must be one of.*omission"),
        (planted_input(defect_field=""), "defect_field"),
        (planted_input(expected_decision="approve"), "expected_decision"),
        (planted_input(expected_decision="edit:product"), "must name the defect_field"),
        (planted_input(author=" "), "author"),
        (planted_input(author="​"), "author"),
        (planted_input(notes=5), "notes"),
        (planted_input(payload=[1]), "payload must be the whole item"),
        (damaged(classification="huge"), "payload.classification: must be one of"),
        (damaged(lot_number="LOT-B20417\n"), "payload.lot_number: must match"),
        (damaged(note="x"), "unexpected property 'note'"),
        (damaged(occurred_on=__import__("datetime").date.min), "put dates and times in quotes"),
        (planted_input(correct_value=float("nan")), "correct_value may hold only JSON"),
        (planted_input(correct_value="huge"), "correct_value: must be one of"),
        (planted_input(correct_value="minor"), "there is no defect"),
        (
            planted_input(defect_field="nope", expected_decision="reject"),
            "not a property in the item schema",
        ),
        (damaged(root_cause="Filed as RP-RESERVED-1"), "reserved target prefix"),
        (damaged(immediate_actions=["rp-reserved-"]), "reserved target prefix"),
    ],
)
def test_bad_input_is_refused_with_a_clear_message(library, data, message):
    with pytest.raises(PlantedImportError, match=message):
        library.import_data(data)
    assert library.all() == []


def test_every_schema_problem_is_reported_at_once(library):
    with pytest.raises(PlantedImportError) as error:
        library.import_data(damaged(classification="huge", capa_required="yes"))
    assert "payload.classification" in str(error.value)
    assert "payload.capa_required" in str(error.value)


def test_unquoted_yaml_date_gets_a_helpful_message(library, tmp_path):
    text = yaml.safe_dump(planted_input()).replace("'2026-01-15'", "2026-01-15")
    path = tmp_path / "pe.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PlantedImportError, match="put dates and times in quotes") as error:
        library.import_file(path)
    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("pe.yaml", "author: a\nauthor: b\n"),
        ("pe.json", '{"author": "a", "author": "b"}'),
        ("pe.json", '{"correct_value": Infinity}'),
        ("pe.yaml", "payload: ["),
        ("pe.yaml", "!!python/name:os.system ''"),
    ],
)
def test_unreadable_files_are_refused(library, tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PlantedImportError, match="not valid"):
        library.import_file(path)


def test_a_missing_file_is_refused(library, tmp_path):
    with pytest.raises(PlantedImportError, match="cannot read"):
        library.import_file(tmp_path / "absent.yaml")


def test_a_self_referencing_payload_is_refused(library, tmp_path):
    path = tmp_path / "pe.yaml"
    path.write_text(
        yaml.safe_dump(planted_input(correct_value=None)).replace(
            "correct_value: null", "correct_value: &loop [*loop]"
        ),
        encoding="utf-8",
    )
    with pytest.raises(PlantedImportError):
        library.import_file(path)


def test_an_id_cannot_be_reused_to_change_content(library):
    library.import_data(planted_input())
    changed = planted_input()
    changed["payload"]["deviation_id"] = "DEV-2026-0199"
    with pytest.raises(PlantedImportError, match="already exists"):
        library.import_data(changed)


def test_the_same_payload_cannot_be_imported_twice(library):
    library.import_data(planted_input())
    with pytest.raises(PlantedImportError, match="same payload as PE-0001"):
        library.import_data(planted_input(planted_error_id="PE-2"))
    library.retire("PE-0001", "qa-1")
    library.import_data(planted_input(planted_error_id="PE-2"))


def test_an_unusable_item_schema_stops_import(library, folder):
    (folder / "item.schema.json").write_text('{"oneOf": []}', encoding="utf-8")
    with pytest.raises(PlantedImportError, match="item schema.*cannot be used"):
        library.import_data(planted_input())


# ----------------------------------------------------------------- approval


@pytest.fixture
def imported(library):
    library.import_data(planted_input())
    return library


def test_a_gxp_queue_needs_two_approvers(imported):
    first = imported.approve("PE-0001", "qa-1")
    assert imported.status(first) is Status.AWAITING_SECOND_APPROVAL
    assert available_ids(imported) == []

    second = imported.approve("PE-0001", "  qa-2  ")
    assert imported.status(second) is Status.APPROVED
    assert (second.approved_by, second.second_approved_by) == ("qa-1", "qa-2")
    assert second.approved_at and second.second_approved_at
    assert available_ids(imported) == ["PE-0001"]


def test_a_non_gxp_queue_needs_one_approver(imported, registry_db, config):
    library = with_gxp(imported, registry_db, config, gxp=False)
    assert available_ids(library) == []
    library.approve("PE-0001", "qa-1")
    assert available_ids(library) == ["PE-0001"]
    with pytest.raises(ApprovalError, match="already has the approvals"):
        library.approve("PE-0001", "qa-2")


def test_making_a_queue_gxp_holds_back_single_approvals(imported, registry_db, config):
    with_gxp(imported, registry_db, config, gxp=False).approve("PE-0001", "qa-1")
    assert available_ids(imported) == []  # the demo queue is GxP
    imported.approve("PE-0001", "qa-2")
    assert available_ids(imported) == ["PE-0001"]


@pytest.mark.parametrize("name", ["author-1", " AUTHOR-1 ", "Author​-1", "ａuthor-1"])
def test_authors_cannot_approve_their_own(imported, name):
    with pytest.raises(ApprovalError, match="their own"):
        imported.approve("PE-0001", name)


@pytest.mark.parametrize("again", ["qa-é", " QA-É ", "qa-é", "QA-É​"])
def test_one_person_cannot_approve_twice(imported, again):
    imported.approve("PE-0001", "qa-é")
    with pytest.raises(ApprovalError, match="already approved"):
        imported.approve("PE-0001", again)
    assert available_ids(imported) == []


@pytest.mark.parametrize("name", ["", "  ", "​", None, 3])
def test_an_approver_needs_a_name(imported, name):
    with pytest.raises(ApprovalError, match="non-empty"):
        imported.approve("PE-0001", name)


def test_an_unknown_id(imported):
    with pytest.raises(UnknownPlantedError):
        imported.approve("PE-9999", "qa-1")
    with pytest.raises(UnknownPlantedError):
        imported.retire("PE-9999", "qa-1")
    with pytest.raises(UnknownPlantedError, match="not configured"):
        imported.available("nope")


def test_forged_approvals_in_the_database_do_not_count(imported, registry_db):
    # SQLite compares names only roughly. Someone writing SQL directly could pass its check with
    # two spellings of one name; the library must still see one person.
    registry_db.execute(
        "UPDATE planted_errors SET approved_by = 'qa-é', approved_at = ?, "
        "second_approved_by = 'qa-É', second_approved_at = ?",
        ["2026-11-26T10:00:00.000000Z"] * 2,
    )
    planted = imported.get("PE-0001")
    assert planted.approvers == frozenset({"qa-é"})
    assert imported.status(planted) is Status.AWAITING_SECOND_APPROVAL
    assert available_ids(imported) == []
    with pytest.raises(ApprovalError, match="both approvals are used"):
        imported.approve("PE-0001", "qa-3")


def test_retirement_is_permanent_and_attributed(imported):
    imported.approve("PE-0001", "qa-1")
    imported.approve("PE-0001", "qa-2")
    retired = imported.retire("PE-0001", "qa-3", reason="a reviewer spotted it")
    assert imported.status(retired) is Status.RETIRED
    assert (retired.retired_by, retired.retire_reason) == ("qa-3", "a reviewer spotted it")
    assert available_ids(imported) == []
    with pytest.raises(ApprovalError, match="already retired"):
        imported.retire("PE-0001", "qa-3")
    with pytest.raises(ApprovalError, match="is retired"):
        imported.approve("PE-0001", "qa-4")


def test_retiring_needs_a_name(imported):
    with pytest.raises(ApprovalError, match="retired_by"):
        imported.retire("PE-0001", " ")


# ----------------------------------------------------------------- after the settings change


@pytest.fixture
def approved(imported):
    imported.approve("PE-0001", "qa-1")
    imported.approve("PE-0001", "qa-2")
    return imported


def test_a_schema_change_holds_back_planted_errors_that_no_longer_fit(approved, folder):
    assert approved.stale(QUEUE) == {}
    schema = json.loads((folder / "item.schema.json").read_text(encoding="utf-8"))
    schema["required"].append("site")
    schema["properties"]["site"] = {"type": "string"}
    (folder / "item.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    assert available_ids(approved) == []
    assert "missing required property 'site'" in approved.stale(QUEUE)["PE-0001"][0]


def test_a_stale_planted_error_cannot_be_approved(imported, folder):
    (folder / "item.schema.json").write_text('{"required": ["site"]}', encoding="utf-8")
    with pytest.raises(ApprovalError, match="cannot be approved"):
        imported.approve("PE-0001", "qa-1")


def test_a_queue_removed_from_the_settings(approved, registry_db, config):
    library = PlantedLibrary(registry_db, dataclasses.replace(config, queues={}))
    planted = library.get("PE-0001")
    assert library.problems(planted) == ["queue 'deviation-triage' is no longer configured"]
    assert library.status(planted) is Status.APPROVED  # judged by the stricter rule
    with pytest.raises(ApprovalError, match="no longer configured"):
        library.approve("PE-0001", "qa-3")


# ----------------------------------------------------------------- failures


def test_a_damaged_row_stops_the_library_rather_than_being_skipped(approved, registry_db):
    with triggers_removed(registry_db, "planted_errors"):
        registry_db.execute("UPDATE planted_errors SET payload = 'not json'")
    with pytest.raises(StoreError, match="damaged"):
        approved.available(QUEUE)


def test_a_missing_table_raises_store_error(library, registry_db):
    registry_db.execute("PRAGMA foreign_keys = OFF")
    registry_db.execute("DROP TABLE planted_errors")
    with pytest.raises(StoreError, match="cannot read"):
        library.all()
    with pytest.raises(StoreError):
        library.import_data(planted_input())
    assert not registry_db.in_transaction


def test_a_locked_database_raises_store_error(library, planted_path):
    from reviewproof.store import open_planted_db

    other = open_planted_db(planted_path)
    other.execute("BEGIN IMMEDIATE")
    try:
        library._db.execute("PRAGMA busy_timeout = 10")
        with pytest.raises(StoreError, match="cannot write"):
            library.import_data(planted_input())
    finally:
        other.execute("ROLLBACK")
        other.close()


def test_a_callers_own_transaction_is_left_alone(library, registry_db):
    registry_db.execute("BEGIN")
    registry_db.execute("CREATE TABLE callers_work (x)")
    with pytest.raises(StoreError, match="already in a transaction"):
        library.import_data(planted_input())
    assert registry_db.in_transaction
    registry_db.execute("COMMIT")
    assert registry_db.execute("SELECT COUNT(*) FROM callers_work").fetchone()[0] == 0
