"""Optional helpers that draft a planted error from a correct item you supply (ADR-003).

A draft is an ordinary input file. Nothing here stores, approves or injects anything: review the
file, edit it, then import it. The source item must be synthetic or de-identified. These helpers
never read live items.

Each helper changes one top-level property and nothing else.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from reviewproof.config import QueueConfig
from reviewproof.itemschema import ItemSchema, json_equal
from reviewproof.models import DefectClass

_HEADER = """\
# DRAFT planted error. Review and edit before importing.
#
#  [ ] Identifiers and dates: this is a copy of the source item. Change anything that would
#      match a real record or an item a reviewer has already seen.
#  [ ] Other fields: do any still give the defect away, or contradict it in a way a real
#      AI proposal would not? A rationale usually needs rewording.
#  [ ] Exactly one defect: apart from the defect, is the item still correct?
#  [ ] Detectable: can a careful reviewer find it from what they see or are expected to check?
#  [ ] No real data: synthetic or de-identified values only.
#
# Keep this file secret and out of shared folders and public repositories.
"""


class DraftError(ValueError):
    """A draft cannot be made from these inputs. The message says why."""


def draft_value_swap(
    queue: QueueConfig,
    source: Mapping[str, Any],
    *,
    planted_error_id: str,
    author: str,
    field: str,
    new_value: Any,
    expected_decision: str | None = None,
) -> dict[str, Any]:
    """Replace one value, such as a dose, date, lot or subject ID."""
    schema = _checked_source(queue, source, field)
    if json_equal(source[field], new_value):
        raise DraftError(f"new_value is the same as the current {field}")
    payload = {**copy.deepcopy(dict(source)), field: copy.deepcopy(new_value)}
    return _draft(
        queue,
        schema,
        source,
        payload,
        DefectClass.WRONG_VALUE,
        planted_error_id,
        author,
        field,
        expected_decision,
    )


def draft_omission(
    queue: QueueConfig,
    source: Mapping[str, Any],
    *,
    planted_error_id: str,
    author: str,
    field: str,
    index: int | None = None,
    expected_decision: str | None = None,
) -> dict[str, Any]:
    """Remove one entry from a list (give ``index``), or a whole property the schema allows
    to be absent.

    The result must still match the item schema: a malformed item would be easy to spot.
    """
    schema = _checked_source(queue, source, field)
    payload = copy.deepcopy(dict(source))
    if index is None:
        if isinstance(payload[field], list):
            raise DraftError(f"{field} is a list: give the index of the entry to remove")
        del payload[field]
    else:
        entries = payload[field]
        if not isinstance(entries, list):
            raise DraftError(f"{field} is not a list, so it has no entry to remove")
        if type(index) is not int or not 0 <= index < len(entries):
            raise DraftError(f"index must be from 0 to {len(entries) - 1}")
        del entries[index]
    return _draft(
        queue,
        schema,
        source,
        payload,
        DefectClass.OMISSION,
        planted_error_id,
        author,
        field,
        expected_decision,
    )


def draft_classification_flip(
    queue: QueueConfig,
    source: Mapping[str, Any],
    *,
    planted_error_id: str,
    author: str,
    field: str,
    to: Any = None,
    expected_decision: str | None = None,
) -> dict[str, Any]:
    """Change a classification to another of the values the schema allows.

    Without ``to``, it takes the value listed before the current one in the schema's ``enum``,
    or the next one for the first value. List classes from least to most severe and the default
    under-classifies, which is usually the riskier error.
    """
    schema = _checked_source(queue, source, field)
    choices = schema.allowed_values(field)
    if len(choices) < 2:
        raise DraftError(f"{field} needs an enum of two or more values in the item schema")
    if to is None:
        position = next(i for i, value in enumerate(choices) if json_equal(value, source[field]))
        to = choices[position - 1] if position else choices[1]
    if not any(json_equal(to, value) for value in choices):
        raise DraftError(f"'to' must be one of {', '.join(map(repr, choices))}")
    if json_equal(source[field], to):
        raise DraftError(f"'to' is the same as the current {field}")
    payload = {**copy.deepcopy(dict(source)), field: to}
    return _draft(
        queue,
        schema,
        source,
        payload,
        DefectClass.WRONG_CLASSIFICATION,
        planted_error_id,
        author,
        field,
        expected_decision,
    )


def write_draft(draft: Mapping[str, Any], path: str | Path) -> None:
    """Write a draft as YAML with a review checklist. Never overwrites a file."""
    body = yaml.safe_dump(dict(draft), sort_keys=False, allow_unicode=True, width=100)
    with Path(path).open("x", encoding="utf-8", newline="\n") as file:
        file.write(_HEADER + body)


def _checked_source(queue: QueueConfig, source: Mapping[str, Any], field: str) -> ItemSchema:
    schema = ItemSchema.load(queue.item_schema)
    problems = schema.errors(dict(source), where="source")
    if problems:
        raise DraftError(f"the source item does not match the schema: {'; '.join(problems)}")
    if field not in schema.property_names:
        raise DraftError(
            f"{field!r} is not a property in the item schema. It has: "
            f"{', '.join(sorted(schema.property_names))}"
        )
    if field not in source:
        raise DraftError(f"the source item has no {field!r}")
    return schema


def _draft(
    queue: QueueConfig,
    schema: ItemSchema,
    source: Mapping[str, Any],
    payload: dict[str, Any],
    defect_class: DefectClass,
    planted_error_id: str,
    author: str,
    field: str,
    expected_decision: str | None,
) -> dict[str, Any]:
    problems = schema.errors(payload)
    if problems:
        raise DraftError(f"the result would not match the item schema: {'; '.join(problems)}")
    expected = expected_decision or f"edit:{field}"
    if expected not in ("reject", f"edit:{field}"):
        raise DraftError(f"expected_decision must be 'reject' or 'edit:{field}'")
    return {
        "planted_error_id": planted_error_id,
        "queue": queue.name,
        "defect_class": defect_class.value,
        "defect_field": field,
        "expected_decision": expected,
        "correct_value": copy.deepcopy(source[field]),
        "author": author,
        "payload": payload,
    }
