"""Drafting planted errors from real agent output (ADR-009).

The organisation exports items from its own systems. These functions turn one of them into a
planted-error file: identifiers replaced, dates shifted, one defect added by a person, and
searched for real data before it can be finished. **What enters ``planted.db`` cannot be taken
out,** so everything here happens before import.

**This cannot make an item anonymous.** It finds the identifiers a handling profile lists and
the terms it is given, and nothing that nobody told it about. A person must read the whole item.

Nothing here opens a ReviewProof database, reads a queue or uses the network. It reads the
files it is given, never changes them, and never overwrites a file. **No message repeats a
value from them,** because terminals and logs are kept: messages name a property, a position or
a line number.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import string
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from reviewproof import __version__, mutators
from reviewproof.canonical import canonical_json, sha256_hex, utc_now
from reviewproof.config import Config, QueueConfig
from reviewproof.itemschema import ItemSchema, SchemaError, json_equal
from reviewproof.models import DefectClass
from reviewproof.planted import (
    REAL_OUTPUT,
    PlantedImportError,
    PlantedLibrary,
    _person,
)
from reviewproof.strictload import LoadError, load_file

WORKING_KEY = "craft_working_file"
#: Values this short are matched only as whole words, or they would match almost anything.
SHORT = 4
#: Fewest words for a sentence copied from the original to be worth a warning.
SENTENCE_WORDS = 4
MUTATORS = ("value-swap", "omission", "classification-flip")

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PLACEHOLDER = re.compile(r"\{([1-9]|1\d|20) (digits|letters)\}")
_WORKING_REQUIRED = frozenset(
    {
        WORKING_KEY,
        "queue",
        "export_item",
        "export_sha256",
        "new_values",
        "shift_days",
        "planted_error_id",
        "author",
        "defect_class",
        "defect_field",
        "expected_decision",
        "payload",
    }
)
_WORKING_OPTIONAL = frozenset({"correct_value", "notes", "purpose"})

_WORKING_HEADER = """\
# WORKING FILE. IT MAY STILL HOLD REAL INFORMATION. Protect it like the export, and destroy it
# when you are done. It cannot be imported.
#
# Add exactly ONE defect: set defect_class, defect_field and expected_decision, and change that
# property of payload (or use rp craft defect). Reword any text that no longer fits. Then run
# rp craft check.
"""
_FINISHED_HEADER = """\
# Planted error drafted from real output. Keep it secret.
#
# Finishing it does NOT show that it holds no real information, only that the checks found none
# of what they were told to look for, and that the people named below say they read it.
# Each approver confirms again that they find no real information in it.
"""


class CraftError(ValueError):
    """A step was refused. The message never repeats a value from the files."""


class Handling(str, Enum):
    #: An identifier: a new value replaces it everywhere in the item.
    REPLACE = "replace"
    #: A date written YYYY-MM-DD: all are moved by the same number of days.
    SHIFT_DATE = "shift_date"
    #: Identifies no person and no record, so it is copied.
    KEEP = "keep"
    #: Free text, lists and objects: copied, searched, and read by a person.
    REVIEW = "review"


#: A change to a property handled in one of these ways, other than the defect, is a second one.
_FIXED = (Handling.REPLACE, Handling.SHIFT_DATE, Handling.KEEP)


@dataclass(frozen=True)
class Profile:
    """How each property of a queue's items is handled, and what must never appear."""

    queue: str
    handling: Mapping[str, Handling]
    patterns: Mapping[str, str]
    blocked_terms: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class Export:
    items: tuple[Mapping[str, Any], ...] = field(repr=False)
    sha256: str


@dataclass(frozen=True)
class Finding:
    """A warning a person must acknowledge. Its code is tied to the exact text it is about."""

    code: str
    message: str


@dataclass(frozen=True)
class Report:
    #: Why the draft cannot be finished.
    blocks: tuple[str, ...]
    warnings: tuple[Finding, ...]
    #: Properties that differ from the original with the profile applied.
    changed: tuple[str, ...]
    #: What would be imported, without provenance. None while something blocks it.
    data: Mapping[str, Any] | None = field(default=None, repr=False)


# ----------------------------------------------------------------- matching


def _separator(character: str) -> bool:
    return character.isspace() or character in "_\u2212" or unicodedata.category(character) == "Pd"


def _fold(text: str, keep_separators: bool = False) -> tuple[str, list[int]]:
    """``text`` made comparable: without case, accents, invisible characters or, unless kept,
    spaces, hyphens and underscores. Also returns, for each character, its place in ``text``.
    """
    folded, places = [], []
    for place, original in enumerate(text):
        for character in unicodedata.normalize("NFKD", original).casefold():
            if unicodedata.category(character) in ("Mn", "Cf"):
                continue
            if not keep_separators and _separator(character):
                continue
            folded.append(character)
            places.append(place)
    return "".join(folded), places


def find(value: str, text: str) -> list[tuple[int, int]]:
    """Where ``value`` appears in ``text``, as (start, end) places in ``text``. Case, spaces,
    hyphens, underscores, accents and different forms of the same character are ignored. A value
    of fewer than ``SHORT`` characters must stand as a whole word.
    """
    needle = _fold(value)[0]
    if not needle:
        return []
    if len(needle) < SHORT:
        haystack, places = _fold(text, keep_separators=True)
        word = re.escape(_fold(value, keep_separators=True)[0].strip())
        pattern = rf"(?<![^\W_]){word}(?![^\W_])"
    else:
        haystack, places = _fold(text)
        pattern = re.escape(needle)
    found = []
    for match in re.finditer(pattern, haystack):
        start, end = places[match.start()], places[match.end() - 1] + 1
        while end < len(text) and unicodedata.category(text[end]) == "Mn":
            end += 1  # an accent that belongs to the last character matched
        found.append((start, end))
    return found


def _replace(text: str, old: str, new: str) -> str:
    pieces, done = [], 0
    for start, end in find(old, text):
        if start >= done:
            pieces += [text[done:start], new]
            done = end
    return "".join([*pieces, text[done:]])


def _texts(value: Any) -> Iterator[str]:
    """Every piece of text in a JSON value: strings, keys, and numbers as written."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, element in value.items():
            yield str(key)
            yield from _texts(element)
    elif isinstance(value, (list, tuple)):
        for element in value:
            yield from _texts(element)
    elif value is not None and not isinstance(value, bool):
        yield str(value)


def _strings(value: Any) -> Iterator[str]:
    """Every string value, keys left out."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for element in value.values():
            yield from _strings(element)
    elif isinstance(value, (list, tuple)):
        for element in value:
            yield from _strings(element)


def _each_string(value: Any, change) -> Any:
    if isinstance(value, str):
        return change(value)
    if isinstance(value, Mapping):
        return {key: _each_string(element, change) for key, element in value.items()}
    if isinstance(value, list):
        return [_each_string(element, change) for element in value]
    return value


# ----------------------------------------------------------------- reading files


def _load(path: str | Path, what: str) -> Any:
    """``load_file`` without its messages, which can quote the file."""
    try:
        return load_file(Path(path))
    except LoadError as error:
        cause = error.__cause__
        mark = getattr(cause, "problem_mark", None)
        if isinstance(cause, json.JSONDecodeError):
            why = f"it is not valid JSON at line {cause.lineno}"
        elif mark is not None:
            why = f"it is not valid at line {mark.line + 1}, for example a repeated key"
        elif isinstance(cause, (OSError, UnicodeDecodeError)):
            why = "it cannot be read as UTF-8 text"
        else:
            why = "it is not valid, for example a key is repeated"
        raise CraftError(f"cannot read the {what}: {why}") from None


def _schema(queue: QueueConfig) -> ItemSchema:
    try:
        return ItemSchema.load(queue.item_schema)
    except SchemaError as error:
        raise CraftError(f"the item format cannot be used: {error}") from error


def _queue(config: Config, name: Any) -> QueueConfig:
    queue = config.queues.get(name) if isinstance(name, str) else None
    if queue is None:
        raise CraftError(f"unknown queue. Configured: {', '.join(sorted(config.queues))}")
    return queue


def _fit_problems(schema: ItemSchema, item: Mapping[str, Any]) -> list[str]:
    """Why ``item`` does not fit the item format, naming only properties the format has."""
    problems = []
    if any(name not in schema.property_names for name in item):
        problems.append("has a property the item format does not have")
    problems += [
        f"'{name}' does not fit the item format"
        for name in sorted(schema.property_names)
        if name in item and schema.property_errors(name, item[name], name)
    ]
    if not problems and schema.errors(dict(item)):
        problems.append("does not fit the item format, for example a property is missing")
    return problems


def load_profile(path: str | Path, config: Config) -> Profile:
    """A handling profile. **Every property of the item format must be listed.**"""
    data = _load(path, "handling profile")
    if not isinstance(data, dict) or set(data) != {"queue", "properties", "blocked_terms"}:
        raise CraftError("a handling profile holds exactly queue, properties and blocked_terms")
    queue = _queue(config, data["queue"])
    names = _schema(queue).property_names
    properties = data["properties"]
    if not isinstance(properties, dict):
        raise CraftError("properties must list how each property is handled")
    missing = sorted(names - set(properties))
    if missing:
        raise CraftError(
            f"the profile does not say how to handle: {', '.join(missing)}. Nothing is made "
            "until every property is listed."
        )
    if set(properties) - names:
        raise CraftError("the profile lists a property the item format does not have")
    handling, patterns = {}, {}
    for name, entry in properties.items():
        pattern = None
        if isinstance(entry, dict):
            if set(entry) != {"handling", "pattern"}:
                raise CraftError(f"'{name}': give handling, or handling and pattern")
            entry, pattern = entry["handling"], entry["pattern"]
        try:
            handling[name] = Handling(entry)
        except ValueError:
            allowed = ", ".join(member.value for member in Handling)
            raise CraftError(f"'{name}': handling must be one of: {allowed}") from None
        if pattern is not None:
            if handling[name] is not Handling.REPLACE:
                raise CraftError(f"'{name}': only a replaced value takes a pattern")
            rest = _PLACEHOLDER.sub("", pattern) if isinstance(pattern, str) else "{"
            if "{" in rest or "}" in rest or not _PLACEHOLDER.search(pattern):
                raise CraftError(
                    f"'{name}': a pattern is text with places such as {{4 digits}} or {{2 letters}}"
                )
            patterns[name] = pattern
    terms = data["blocked_terms"]
    if not isinstance(terms, list) or not all(
        isinstance(t, str) and len(_fold(t)[0]) >= 2 for t in terms
    ):
        raise CraftError(
            "blocked_terms must be a list of words or phrases of two characters or more. It "
            "may be empty, but you must say so with []."
        )
    return Profile(queue.name, handling, patterns, tuple(terms))


def load_export(path: str | Path, config: Config, profile: Profile) -> Export:
    """The organisation's export: a JSON list of items in the queue's item format, and nothing
    more.
    """
    path = Path(path)
    if path.suffix.lower() != ".json":
        raise CraftError("the export must be a .json file holding a list of items")
    data = _load(path, "export")
    if not isinstance(data, list) or not data:
        raise CraftError("the export must be a list of items")
    schema = _schema(_queue(config, profile.queue))
    for number, item in enumerate(data):
        where = f"export item {number}"
        if not isinstance(item, dict):
            raise CraftError(f"{where} is not an item")
        problems = _fit_problems(schema, item)
        if problems:
            raise CraftError(f"{where}: {'; '.join(problems)}")
        for name, value in item.items():
            how = profile.handling[name]
            if how is Handling.REPLACE and type(value) not in (str, int):
                raise CraftError(f"{where}: '{name}' is replaced, so it must be text or a number")
            if how is Handling.SHIFT_DATE and _as_date(value) is None:
                raise CraftError(f"{where}: '{name}' is shifted, so it must be written YYYY-MM-DD")
    return Export(tuple(data), sha256_hex(path.read_bytes()))


def _as_date(value: Any) -> date | None:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def load_working(path: str | Path) -> dict[str, Any]:
    data = _load(path, "working file")
    if not isinstance(data, dict) or data.get(WORKING_KEY) != 1:
        raise CraftError("this is not a working file made by rp craft start")
    missing = sorted(_WORKING_REQUIRED - set(data))
    if missing:
        raise CraftError(f"the working file lacks: {', '.join(missing)}")
    if set(data) - _WORKING_REQUIRED - _WORKING_OPTIONAL:
        raise CraftError("the working file has a key it should not have")
    return data


# ----------------------------------------------------------------- the original, transformed


def _identifiers(export: Export, profile: Profile) -> list[tuple[str, str, int]]:
    """(property, value, export item) for every replaced value of every exported item."""
    return [
        (name, str(item[name]), number)
        for number, item in enumerate(export.items)
        for name, how in profile.handling.items()
        if how is Handling.REPLACE and name in item and str(item[name]).strip()
    ]


def _check_new_value(
    export: Export, profile: Profile, schema: ItemSchema, name: str, value: Any
) -> str | None:
    """Why ``value`` cannot replace an identifier, or None."""
    if type(value) not in (str, int) or schema.property_errors(name, value, name):
        return "does not fit the item format"
    text = str(value)
    if any(find(old, text) for _, old, _ in _identifiers(export, profile)):
        return "holds an identifier from the export"
    if any(find(term, text) for term in profile.blocked_terms):
        return "holds a blocked term"
    return None


def _generate(pattern: str) -> str:
    def digits_or_letters(match: re.Match) -> str:
        alphabet = string.digits if match[2] == "digits" else string.ascii_uppercase
        return "".join(secrets.choice(alphabet) for _ in range(int(match[1])))

    return _PLACEHOLDER.sub(digits_or_letters, pattern)


def _new_values(
    export: Export,
    profile: Profile,
    schema: ItemSchema,
    item: Mapping[str, Any],
    given: Mapping[str, Any],
) -> dict[str, Any]:
    needed = [n for n, how in profile.handling.items() if how is Handling.REPLACE and n in item]
    if set(given) - set(needed):
        raise CraftError(
            f"new values are only for the replaced properties of this item: {', '.join(needed)}"
        )
    chosen = {}
    for name in needed:
        if name in given:
            value = given[name]
            if type(item[name]) is int and isinstance(value, str) and value.isdigit():
                value = int(value)
            problem = _check_new_value(export, profile, schema, name, value)
            if problem:
                raise CraftError(f"the new value for '{name}' {problem}")
        elif name in profile.patterns and type(item[name]) is str:
            for _ in range(200):
                value = _generate(profile.patterns[name])
                problem = _check_new_value(export, profile, schema, name, value)
                if problem is None:
                    break
            else:
                raise CraftError(f"the pattern for '{name}' made no usable value: it {problem}")
        else:
            raise CraftError(f"give a new value for '{name}' with --set {name}=VALUE")
        chosen[name] = value
    return chosen


def _shifted(profile: Profile, item: Mapping[str, Any], days: Any) -> dict[str, str]:
    dated = [n for n, how in profile.handling.items() if how is Handling.SHIFT_DATE and n in item]
    if not dated:
        return {}
    if type(days) is not int or days == 0:
        raise CraftError("give the number of days to move dates by, with --shift-days (not 0)")
    try:
        moved = {n: (_as_date(item[n]) + timedelta(days=days)).isoformat() for n in dated}
    except OverflowError:
        raise CraftError("--shift-days moves a date out of range") from None
    originals = {item[n] for n in dated}
    if originals & set(moved.values()):
        raise CraftError("--shift-days moves a date onto another original date: choose another")
    return moved


def _base(
    profile: Profile, item: Mapping[str, Any], new_values: Mapping, moved: Mapping
) -> dict[str, Any]:
    """The original with the profile applied: identifiers replaced everywhere in it, and dates
    shifted. The longest values first, so that one inside another is not replaced alone.
    """
    swaps = [(str(item[n]), str(v)) for n, v in new_values.items() if str(item[n]).strip()]
    swaps += [(item[n], v) for n, v in moved.items()]
    swaps.sort(key=lambda swap: len(swap[0]), reverse=True)

    def change(text: str) -> str:
        for old, new in swaps:
            text = _replace(text, old, new)
        return text

    base = {}
    for name, value in item.items():
        if name in new_values:
            base[name] = new_values[name]
        elif name in moved:
            base[name] = moved[name]
        else:
            base[name] = _each_string(copy.deepcopy(value), change)
    return base


# ----------------------------------------------------------------- the steps


def start(
    config: Config,
    profile: Profile,
    export: Export,
    item_number: int,
    *,
    planted_error_id: str,
    author: str,
    new_values: Mapping[str, Any] | None = None,
    shift_days: int | None = None,
) -> dict[str, Any]:
    """A working file's content: one exported item with the profile applied, and no defect yet.
    **It may still hold real information.**
    """
    if type(item_number) is not int or not 0 <= item_number < len(export.items):
        raise CraftError(f"--item must be from 0 to {len(export.items) - 1}")
    if not isinstance(planted_error_id, str) or not _ID.fullmatch(planted_error_id):
        raise CraftError("the ID must be 1-64 letters, digits, dots, hyphens or underscores")
    if not isinstance(author, str) or not _person(author):
        raise CraftError("the author must be a non-empty ID")
    schema = _schema(_queue(config, profile.queue))
    item = export.items[item_number]
    chosen = _new_values(export, profile, schema, item, new_values or {})
    moved = _shifted(profile, item, shift_days)
    return {
        WORKING_KEY: 1,
        "queue": profile.queue,
        "export_item": item_number,
        "export_sha256": export.sha256,
        "new_values": chosen,
        "shift_days": shift_days if moved else None,
        "planted_error_id": planted_error_id,
        "author": author.strip(),
        "defect_class": None,
        "defect_field": None,
        "expected_decision": None,
        "notes": None,
        "payload": schema.arrange(_base(profile, item, chosen, moved)),
    }


def _rebuild(
    config: Config, profile: Profile, export: Export, working: Mapping[str, Any]
) -> tuple[ItemSchema, Mapping[str, Any], dict[str, Any], list[str], list[str]]:
    """(item format, original item, original with the profile applied, new identifiers,
    shifted dates) for a working file, checking it still belongs to this export.
    """
    if working["queue"] != profile.queue:
        raise CraftError("the working file and the profile are for different queues")
    if working["export_sha256"] != export.sha256:
        raise CraftError("this is not the export the working file was started from")
    number = working["export_item"]
    if type(number) is not int or not 0 <= number < len(export.items):
        raise CraftError("the working file names an item the export does not have")
    schema = _schema(_queue(config, profile.queue))
    item = export.items[number]
    replaced = {n for n, how in profile.handling.items() if how is Handling.REPLACE and n in item}
    if not isinstance(working["new_values"], dict) or set(working["new_values"]) != replaced:
        raise CraftError("new_values must hold exactly the replaced properties of this item")
    chosen = _new_values(export, profile, schema, item, working["new_values"])
    moved = _shifted(profile, item, working["shift_days"])
    base = _base(profile, item, chosen, moved)
    return schema, item, base, [str(v) for v in chosen.values()], list(moved.values())


def add_defect(
    config: Config,
    profile: Profile,
    export: Export,
    working: Mapping[str, Any],
    mutator: str,
    field_name: str,
    *,
    new_value: Any = None,
    index: int | None = None,
    to: Any = None,
) -> dict[str, Any]:
    """The working file with one defect added by a drafting helper. A person chooses it."""
    schema = _rebuild(config, profile, export, working)[0]
    if working["defect_field"] is not None:
        raise CraftError("this draft already has a defect: a planted error has exactly one")
    payload = working["payload"]
    if not isinstance(payload, dict) or _fit_problems(schema, payload):
        raise CraftError("the payload does not fit the item format: run rp craft check")
    queue = _queue(config, profile.queue)
    shared = {
        "planted_error_id": working["planted_error_id"],
        "author": working["author"],
        "field": field_name,
    }
    try:
        if mutator == "value-swap":
            draft = mutators.draft_value_swap(queue, payload, new_value=new_value, **shared)
        elif mutator == "omission":
            draft = mutators.draft_omission(queue, payload, index=index, **shared)
        elif mutator == "classification-flip":
            draft = mutators.draft_classification_flip(queue, payload, to=to, **shared)
        else:
            raise CraftError(f"the mutator must be one of: {', '.join(MUTATORS)}")
    except mutators.DraftError as error:
        # The payload fits the item format, so these messages describe only the request.
        raise CraftError(str(error)) from None
    return {
        **working,
        "defect_class": draft["defect_class"],
        "defect_field": draft["defect_field"],
        "expected_decision": draft["expected_decision"],
        "payload": draft["payload"],
    }


def check(config: Config, profile: Profile, export: Export, working: Mapping[str, Any]) -> Report:
    """What stops the draft being finished, and what a person must acknowledge."""
    schema, original, base, new_identifiers, moved = _rebuild(config, profile, export, working)
    payload = working["payload"]
    if not isinstance(payload, dict):
        raise CraftError("the payload must be the whole item, as a mapping")
    blocks = [f"payload {problem}" for problem in _fit_problems(schema, payload)]

    defect = working["defect_field"]
    if defect is None or working["defect_class"] is None or working["expected_decision"] is None:
        blocks.append("no defect yet: set defect_class, defect_field and expected_decision")
    elif defect not in profile.handling:
        blocks.append("defect_field is not a property of the item format")
        defect = None
    changed = tuple(name for name in profile.handling if not _same(payload, base, name))
    blocks += [
        f"'{name}' is handled as {profile.handling[name].value} and differs from the original: "
        "that would be a second defect"
        for name in changed
        if name != defect and profile.handling[name] in _FIXED
    ]
    if defect is not None and defect not in changed:
        blocks.append(f"'{defect}' is unchanged from the original, so there is no defect")

    final = {
        name: working[name]
        for name in (
            "planted_error_id",
            "queue",
            "defect_class",
            "defect_field",
            "expected_decision",
            "author",
        )
    }
    final["payload"] = payload
    if "correct_value" in working:
        final["correct_value"] = working["correct_value"]
    elif defect is not None and defect in base:
        final["correct_value"] = base[defect]
    for name in ("notes", "purpose"):
        if working.get(name) is not None:
            final[name] = working[name]

    blocks += _found(profile, export, original, final)
    if not blocks:
        try:
            PlantedLibrary.check_data(config, final)
        except PlantedImportError as error:
            text = str(error)
            # A nested property name could itself be real data.
            blocks.append(
                "the correct value or another entry does not fit the item format"
                if "unexpected property" in text
                else text
            )
    warnings = _warnings(profile, original, base, final, [*new_identifiers, *moved])
    return Report(tuple(blocks), tuple(warnings), changed, None if blocks else final)


def _same(first: Mapping, second: Mapping, name: str) -> bool:
    if (name in first) != (name in second):
        return False
    return name not in first or json_equal(first[name], second[name])


def _places(profile: Profile, final: Mapping[str, Any]) -> Iterator[tuple[str, Any]]:
    """(where, value) for everything in what would be imported that a person wrote."""
    for name, value in final["payload"].items():
        if name in profile.handling:
            yield f"payload.{name}", value
        else:
            yield "an unlisted property of payload", {name: value}
    for name in ("correct_value", "notes", "planted_error_id", "author", "purpose"):
        if name in final:
            yield name, final[name]


def _found(
    profile: Profile, export: Export, original: Mapping, final: Mapping[str, Any]
) -> list[str]:
    """Identifiers of any exported item, blocked terms and unshifted dates."""
    identifiers = _identifiers(export, profile)
    dates = [
        (name, original[name])
        for name, how in profile.handling.items()
        if how is Handling.SHIFT_DATE and name in original
    ]
    blocks: dict[str, None] = {}  # in order, without repeats
    for where, value in _places(profile, final):
        for text in _texts(value):
            for name, identifier, number in identifiers:
                if find(identifier, text):
                    blocks[f"a value of '{name}' from export item {number} appears in {where}"] = (
                        None
                    )
            for number, term in enumerate(profile.blocked_terms, start=1):
                if find(term, text):
                    blocks[f"blocked term number {number} appears in {where}"] = None
            for name, written in dates:
                if find(written, text):
                    blocks[f"an unshifted date from '{name}' appears in {where}"] = None
    return list(blocks)


# ----------------------------------------------------------------- warnings

_MONTH = (
    "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    "sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
#: In this order: what one finds is blanked out before the next looks.
_SUSPECT = (
    ("an email address", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    (
        "a date",
        re.compile(
            r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"
            rf"|\b\d{{1,2}}(?:st|nd|rd|th)?[\s-]+(?:{_MONTH})\b(?:[\s,-]+\d{{2,4}})?"
            rf"|\b(?:{_MONTH})\s+\d{{1,4}}\b",
            re.IGNORECASE,
        ),
    ),
    ("a telephone number", re.compile(r"(?<![\w-])\+?\d(?:[ ()./-]{0,2}\d){6,}(?![\w-])")),
    ("a long number", re.compile(r"\d{5,}")),
)


def _sentences(value: Any) -> Iterator[str]:
    for text in _strings(value):
        for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
            comparable = _fold(sentence, keep_separators=True)[0].strip(" .!?;")
            if len(comparable.split()) >= SENTENCE_WORDS:
                yield comparable


def _warnings(
    profile: Profile,
    original: Mapping[str, Any],
    base: Mapping[str, Any],
    final: Mapping[str, Any],
    allowed: Iterable[str],
) -> list[Finding]:
    reviewed = [n for n, how in profile.handling.items() if how is Handling.REVIEW]
    # Free text, and the defect field whatever its handling: the author types its wrong value,
    # which could be a real one that the export does not hold.
    written = [*reviewed, final.get("defect_field")]
    free = [
        (f"payload.{n}", final["payload"][n])
        for n in dict.fromkeys(written)
        if n in final["payload"]
    ]
    free += [(name, final[name]) for name in ("correct_value", "notes") if name in final]
    before = {s for n in reviewed for source in (original, base) for s in _sentences(source.get(n))}
    # The new identifiers and shifted dates are expected, so they raise nothing.
    allowed = sorted(allowed, key=len, reverse=True)

    found = []
    for where, value in free:
        remaining = list(_texts(value))  # numbers too
        for known in allowed:
            # Only where it stands alone: inside a longer number or word it hides nothing.
            alone = re.compile(rf"(?<![^\W_]){re.escape(known)}(?![^\W_])")
            remaining = [alone.sub(" ", text) for text in remaining]
        for what, pattern in _SUSPECT:
            if any(pattern.search(text) for text in remaining):
                found.append(
                    _finding(what, where, value, f"text that looks like {what} in {where}")
                )
            remaining = [pattern.sub(" ", text) for text in remaining]
        copied = sum(sentence in before for sentence in _sentences(value))
        if copied:
            found.append(
                _finding(
                    "copied",
                    where,
                    value,
                    f"{copied} sentence(s) copied unchanged from the original in {where}",
                )
            )
    if final.get("defect_class") == DefectClass.WRONG_CLASSIFICATION.value and all(
        _same(final["payload"], base, n) for n in reviewed
    ):
        found.append(
            _finding(
                "explanation",
                "payload",
                final["payload"],
                "the classification changed and no explanation was reworded",
            )
        )
    return found


def _finding(kind: str, where: str, value: Any, message: str) -> Finding:
    """The code covers the exact text, so an acknowledgment does not carry over to an edit."""
    try:
        digest = sha256_hex(canonical_json([kind, where, value]))
    except (TypeError, ValueError):
        digest = sha256_hex(repr([kind, where, value]).encode("utf-8"))
    return Finding(f"W-{digest[:12]}", message)


def finish(
    config: Config,
    profile: Profile,
    export: Export,
    working: Mapping[str, Any],
    *,
    source_confirmed_by: str,
    checked_for_real_data_by: str,
    acknowledged: Iterable[str] = (),
    clock=utc_now,
) -> dict[str, Any]:
    """The planted-error file's content, with its provenance. Refused while anything blocks it,
    while a warning is not acknowledged, or unless both checks name someone other than the
    author. ReviewProof cannot verify who is typing.
    """
    report = check(config, profile, export, working)
    if report.blocks:
        raise CraftError("not finished:\n  " + "\n  ".join(report.blocks))
    names = {
        "source_confirmed_by": source_confirmed_by,
        "checked_for_real_data_by": checked_for_real_data_by,
    }
    for what, name in names.items():
        if not isinstance(name, str) or not _person(name):
            raise CraftError(f"{what} must name who did it")
        if _person(name) == _person(working["author"]):
            raise CraftError(f"{what} must be someone other than the author")
        if any(find(identifier, name) for _, identifier, _ in _identifiers(export, profile)) or (
            any(find(term, name) for term in profile.blocked_terms)
        ):
            raise CraftError(f"{what} holds an identifier or a blocked term: use a pseudonymous ID")
    given = set(acknowledged)
    codes = {warning.code for warning in report.warnings}
    if given - codes:
        raise CraftError(
            "an acknowledgment matches no warning now. The text may have changed since it was "
            f"read: {', '.join(sorted(given - codes))}"
        )
    waiting = [w for w in report.warnings if w.code not in given]
    if waiting:
        raise CraftError(
            "read each of these, then acknowledge it with --acknowledge CODE:\n  "
            + "\n  ".join(f"{w.code}  {w.message}" for w in waiting)
        )
    data = {
        **report.data,
        "provenance": {
            "origin": REAL_OUTPUT,
            **{what: name.strip() for what, name in names.items()},
            "tool_version": __version__,
            "drafted_at": clock(),
        },
    }
    try:
        PlantedLibrary.check_data(config, data)
    except PlantedImportError as error:
        raise CraftError(str(error)) from None
    return data


def write_working(working: Mapping[str, Any], path: str | Path) -> None:
    _write(_WORKING_HEADER, working, path)


def write_finished(data: Mapping[str, Any], path: str | Path) -> None:
    _write(_FINISHED_HEADER, data, path)


def _write(header: str, data: Mapping[str, Any], path: str | Path) -> None:
    """Never overwrites a file."""
    body = yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True, width=100)
    with Path(path).open("x", encoding="utf-8", newline="\n") as file:
        file.write(header + body)
