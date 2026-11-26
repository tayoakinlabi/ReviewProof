"""YAML and JSON loading that rejects repeated keys.

Both formats normally keep the last of two equal keys without a word, so a file could say one
thing to the person reading it and another to the program.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


class LoadError(ValueError):
    """The file cannot be read or parsed."""


def load_file(path: Path) -> Any:
    """Load a ``.json`` file as JSON, and any other file as safe YAML."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise LoadError(f"cannot read {path}: {error}") from error
    kind = "JSON" if path.suffix.lower() == ".json" else "YAML"
    try:
        if kind == "JSON":
            return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_no_constant)
        return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - a SafeLoader subclass
    except (ValueError, yaml.YAMLError, RecursionError) as error:
        raise LoadError(f"{path} is not valid {kind}: {error}") from error


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"repeated key {key!r}")
        result[key] = value
    return result


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed")


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects repeated keys instead of silently keeping the last one."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, (str, int, float, bool, type(None))):
            raise yaml.constructor.ConstructorError(
                None, None, "keys must be simple values", key_node.start_mark
            )
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"repeated key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)
