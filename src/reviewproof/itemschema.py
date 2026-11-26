"""A small validator for item schemas, written as a subset of JSON Schema (ADR-001).

It supports only the keywords below and **refuses a schema that uses any other**, so that no
rule is ever silently skipped. Error messages name where the problem is and what was expected,
and do not repeat the values found, which may be sensitive.

Patterns follow JSON Schema's regular-expression dialect where it matters here: ``$`` matches
only at the very end (Python would also match before a final line break), and ``\\d`` and
``\\w`` match ASCII only.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from reviewproof.strictload import LoadError, load_file

_ANNOTATIONS = frozenset(
    {"$schema", "$id", "$comment", "title", "description", "default", "examples", "deprecated"}
)
_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "pattern",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
    }
)
_TYPES = ("object", "array", "string", "number", "integer", "boolean", "null")


class SchemaError(ValueError):
    """The item schema cannot be used."""


class ItemSchema:
    def __init__(self, schema: Any) -> None:
        self._patterns: dict[str, re.Pattern[str]] = {}
        self._check_schema(schema, "#")
        self._schema = schema

    @classmethod
    def load(cls, path: str | Path) -> ItemSchema:
        try:
            return cls(load_file(Path(path)))
        except LoadError as error:
            raise SchemaError(str(error)) from error

    @property
    def property_names(self) -> frozenset[str]:
        """The top-level properties the schema names."""
        if not isinstance(self._schema, dict):
            return frozenset()
        return frozenset(self._schema.get("properties", {}))

    def allowed_values(self, name: str) -> list[Any]:
        """The ``enum`` of one top-level property, or an empty list if it has none."""
        subschema = self._schema["properties"][name]
        return list(subschema.get("enum", [])) if isinstance(subschema, dict) else []

    def arrange(self, instance: Any) -> Any:
        """Return a copy with object keys in the order the schema lists them, then the rest
        by name.

        The order depends only on the schema and the key names, never on where the item came
        from. Real and planted items pass through here, so field order cannot tell them apart.
        """
        return _arrange(self._schema, instance)

    def errors(self, instance: Any, where: str = "payload") -> list[str]:
        """Return every way ``instance`` breaks the schema. An empty list means it is valid."""
        found: list[str] = []
        self._validate(self._schema, instance, where, found)
        return found

    def property_errors(self, name: str, value: Any, where: str) -> list[str]:
        """Validate ``value`` against the schema of one top-level property."""
        found: list[str] = []
        self._validate(self._schema["properties"][name], value, where, found)
        return found

    # ------------------------------------------------------------- the schema itself

    def _check_schema(self, schema: Any, at: str) -> None:
        if isinstance(schema, bool):
            return
        if not isinstance(schema, dict):
            raise SchemaError(f"{at}: a schema must be an object, true or false")
        for keyword, value in schema.items():
            if keyword in _ANNOTATIONS:
                continue
            if keyword not in _KEYWORDS:
                raise SchemaError(
                    f"{at}: unsupported keyword {keyword!r}. Supported: "
                    f"{', '.join(sorted(_KEYWORDS))}"
                )
            self._check_keyword(keyword, value, f"{at}/{keyword}")

    def _check_keyword(self, keyword: str, value: Any, at: str) -> None:
        if keyword == "type":
            names = value if isinstance(value, list) else [value]
            if not names or any(name not in _TYPES for name in names):
                raise SchemaError(f"{at}: must be one or more of {', '.join(_TYPES)}")
        elif keyword == "properties":
            if not isinstance(value, dict) or any(not isinstance(name, str) for name in value):
                raise SchemaError(f"{at}: must be an object with text keys")
            for name, subschema in value.items():
                self._check_schema(subschema, f"{at}/{name}")
        elif keyword in ("additionalProperties", "items"):
            self._check_schema(value, at)
        elif keyword == "required":
            if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
                raise SchemaError(f"{at}: must be a list of property names")
        elif keyword == "enum":
            if not isinstance(value, list) or not value:
                raise SchemaError(f"{at}: must be a non-empty list")
        elif keyword == "pattern":
            if not isinstance(value, str):
                raise SchemaError(f"{at}: must be text")
            try:
                self._patterns[value] = re.compile(_anchor_at_true_end(value), re.ASCII)
            except re.error as error:
                raise SchemaError(f"{at}: not a valid pattern: {error}") from error
        elif keyword in ("minimum", "maximum"):
            if not _is_number(value) or not math.isfinite(value):
                raise SchemaError(f"{at}: must be a number")
        elif type(value) is not int or value < 0:
            raise SchemaError(f"{at}: must be a whole number, 0 or more")

    # ------------------------------------------------------------- instances

    def _validate(self, schema: Any, instance: Any, where: str, found: list[str]) -> None:
        if schema is True:
            return
        if schema is False:
            found.append(f"{where}: is not allowed")
            return

        if "type" in schema:
            names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_has_type(instance, name) for name in names):
                found.append(
                    f"{where}: must be of type {' or '.join(names)}, not {_type_name(instance)}"
                )
                return
        if "enum" in schema and not any(_same(instance, allowed) for allowed in schema["enum"]):
            found.append(f"{where}: must be one of {', '.join(map(repr, schema['enum']))}")

        if isinstance(instance, dict):
            self._validate_object(schema, instance, where, found)
        elif isinstance(instance, list):
            _check_size(schema, len(instance), "minItems", "maxItems", "entries", where, found)
            if "items" in schema:
                for index, element in enumerate(instance):
                    self._validate(schema["items"], element, f"{where}[{index}]", found)
        elif isinstance(instance, str):
            _check_size(schema, len(instance), "minLength", "maxLength", "characters", where, found)
            if "pattern" in schema and not self._patterns[schema["pattern"]].search(instance):
                found.append(f"{where}: must match the pattern {schema['pattern']}")
        elif _is_number(instance):
            if "minimum" in schema and instance < schema["minimum"]:
                found.append(f"{where}: must be at least {schema['minimum']}")
            if "maximum" in schema and instance > schema["maximum"]:
                found.append(f"{where}: must be at most {schema['maximum']}")

    def _validate_object(
        self, schema: Mapping[str, Any], instance: dict, where: str, found: list[str]
    ) -> None:
        for name in schema.get("required", []):
            if name not in instance:
                found.append(f"{where}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                self._validate(properties[name], value, f"{where}.{name}", found)
            elif schema.get("additionalProperties", True) is False:
                found.append(f"{where}: unexpected property {name!r}")
            else:
                extra = schema.get("additionalProperties", True)
                self._validate(extra, value, f"{where}.{name}", found)


def _arrange(schema: Any, instance: Any) -> Any:
    known = schema if isinstance(schema, dict) else {}
    if isinstance(instance, dict):
        properties = known.get("properties", {})
        names = [name for name in properties if name in instance]
        names += sorted(name for name in instance if name not in properties)
        other = known.get("additionalProperties")
        return {name: _arrange(properties.get(name, other), instance[name]) for name in names}
    if isinstance(instance, list):
        return [_arrange(known.get("items"), element) for element in instance]
    return instance


def _check_size(
    schema: Mapping[str, Any],
    size: int,
    least: str,
    most: str,
    unit: str,
    where: str,
    found: list[str],
) -> None:
    if least in schema and size < schema[least]:
        found.append(f"{where}: needs at least {schema[least]} {unit}")
    if most in schema and size > schema[most]:
        found.append(f"{where}: allows at most {schema[most]} {unit}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _has_type(value: Any, name: str) -> bool:
    if name == "number":
        return _is_number(value)
    if name == "integer":
        return _is_number(value) and (isinstance(value, int) or value.is_integer())
    expected = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
    return isinstance(value, expected[name])


def _type_name(value: Any) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _has_type(value, name):
            return name
    return type(value).__name__


def _same(left: Any, right: Any) -> bool:
    """JSON equality: 1 equals 1.0, but true does not equal 1."""
    if _is_number(left) and _is_number(right):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(map(_same, left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    return left == right


json_equal = _same


def _anchor_at_true_end(pattern: str) -> str:
    """Replace each ``$`` anchor with ``\\Z``, leaving escaped ones and character classes."""
    result: list[str] = []
    index, in_class, class_start = 0, False, 0
    while index < len(pattern):
        character = pattern[index]
        if character == "\\":
            result.append(pattern[index : index + 2])
            index += 2
            continue
        if in_class:
            # A "]" straight after "[" or "[^" is a literal.
            first = pattern[class_start + 1 : index] in ("", "^")
            if character == "]" and not first:
                in_class = False
        elif character == "[":
            in_class, class_start = True, index
        elif character == "$":
            character = r"\Z"
        result.append(character)
        index += 1
    return "".join(result)
