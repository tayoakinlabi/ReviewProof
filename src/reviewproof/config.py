"""Systems and queues, loaded from a YAML file.

Loading is strict: an unknown or repeated key is an error, so a typing mistake cannot silently
fall back to a default.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from reviewproof.models import FeedbackTiming, OversightContext, RiskCategory

#: Planted share of all items shown, by risk tier. Proposed defaults, to be calibrated in a pilot.
TIER_RATES: tuple[float, ...] = (0.01, 0.02, 0.03)

#: Above this, a planted error would be needed for more than every real item.
MAX_PLANTED_RATE = 0.5

DEFAULT_RESERVED_TARGET_PREFIX = "rp-reserved-"
MIN_RESERVED_PREFIX_LENGTH = 6

_BASE_RISK = re.compile(r"A([1-9][0-9]?)")


class ConfigError(ValueError):
    """The configuration is invalid. The message says where."""


def risk_tier(base_risk: str, adjusted_category: RiskCategory | None = None) -> int:
    """Return the tier (0, 1 or 2) for a VITAL base risk such as ``A5``.

    A1-A4 is tier 0, A5-A6 tier 1, A7 and above tier 2. A High risk-adjusted category moves a
    queue up one tier, and the top tier stays where it is.
    """
    match = _BASE_RISK.fullmatch(base_risk) if isinstance(base_risk, str) else None
    if match is None:
        raise ConfigError(f"vital_base_risk must look like A1, A5 or A7, not {base_risk!r}")
    level = int(match.group(1))
    tier = 0 if level <= 4 else 1 if level <= 6 else 2
    if adjusted_category is RiskCategory.HIGH:
        tier += 1
    return min(tier, len(TIER_RATES) - 1)


@dataclass(frozen=True)
class QueueConfig:
    name: str
    system: str
    task: str
    vital_base_risk: str
    min_review_seconds: float
    feedback: FeedbackTiming
    item_schema: Path
    vital_adjusted_category: RiskCategory | None = None
    planted_rate_override: float | None = None
    # Defaults to the stricter setting: a GxP queue needs two approvers per planted error.
    gxp: bool = True
    site: str | None = None

    @property
    def tier(self) -> int:
        return risk_tier(self.vital_base_risk, self.vital_adjusted_category)

    @property
    def planted_rate(self) -> float:
        if self.planted_rate_override is not None:
            return self.planted_rate_override
        return TIER_RATES[self.tier]


@dataclass(frozen=True)
class Config:
    systems: Mapping[str, OversightContext]
    queues: Mapping[str, QueueConfig]
    reserved_target_prefix: str = DEFAULT_RESERVED_TARGET_PREFIX


def load_config(path: str | Path) -> Config:
    """Load and validate a configuration file. Relative paths in it are relative to the file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error
    return parse_config(data, base_dir=path.parent)


def parse_config(data: Any, base_dir: Path) -> Config:
    root = _mapping(
        data, "config", required={"systems", "queues"}, optional={"reserved_target_prefix"}
    )

    prefix = root.get("reserved_target_prefix", DEFAULT_RESERVED_TARGET_PREFIX)
    prefix = _string(prefix, "reserved_target_prefix")
    if len(prefix) < MIN_RESERVED_PREFIX_LENGTH or prefix != prefix.strip():
        raise ConfigError(
            f"reserved_target_prefix must be at least {MIN_RESERVED_PREFIX_LENGTH} characters "
            "with no surrounding spaces, so that it cannot match real target IDs by accident"
        )

    systems: dict[str, OversightContext] = {}
    for index, entry in enumerate(_list(root["systems"], "systems")):
        system = _parse_system(entry, f"systems[{index}]")
        if system.system in systems:
            raise ConfigError(f"systems[{index}]: duplicate system name {system.system!r}")
        systems[system.system] = system

    queues: dict[str, QueueConfig] = {}
    for index, entry in enumerate(_list(root["queues"], "queues")):
        queue = _parse_queue(entry, f"queues[{index}]", base_dir)
        if queue.name in queues:
            raise ConfigError(f"queues[{index}]: duplicate queue name {queue.name!r}")
        if queue.system not in systems:
            raise ConfigError(f"queues[{index}]: unknown system {queue.system!r}")
        queues[queue.name] = queue

    return Config(MappingProxyType(systems), MappingProxyType(queues), prefix)


def _parse_system(data: Any, where: str) -> OversightContext:
    required = ("name", "model_id", "model_version", "prompt_version", "tool_schema_hash")
    entry = _mapping(data, where, required=set(required), optional={"intended_use"})
    # Versions must be quoted in YAML: unquoted, 1.10 would be read as the number 1.1.
    values = {key: _string(entry[key], f"{where}.{key}") for key in required}
    intended_use = entry.get("intended_use", "")
    if not isinstance(intended_use, str):
        raise ConfigError(f"{where}.intended_use must be a string")
    return OversightContext(system=values.pop("name"), intended_use=intended_use, **values)


def _parse_queue(data: Any, where: str, base_dir: Path) -> QueueConfig:
    entry = _mapping(
        data,
        where,
        required={
            "name",
            "system",
            "task",
            "vital_base_risk",
            "min_review_seconds",
            "feedback",
            "item_schema",
        },
        optional={"vital_adjusted_category", "planted_rate", "gxp", "site"},
    )

    category = entry.get("vital_adjusted_category")
    if category is not None:
        category = _choice(category, RiskCategory, f"{where}.vital_adjusted_category")

    try:
        risk_tier(entry["vital_base_risk"])
    except ConfigError as error:
        raise ConfigError(f"{where}: {error}") from error

    rate = entry.get("planted_rate")
    if rate is not None:
        rate = _number(rate, f"{where}.planted_rate")
        if not 0 < rate <= MAX_PLANTED_RATE:
            raise ConfigError(
                f"{where}.planted_rate must be above 0 and at most {MAX_PLANTED_RATE} "
                "(0.02 means 2%)"
            )

    min_review = _number(entry["min_review_seconds"], f"{where}.min_review_seconds")
    if min_review < 0:
        raise ConfigError(f"{where}.min_review_seconds must not be negative")

    gxp = entry.get("gxp", True)
    if not isinstance(gxp, bool):
        raise ConfigError(f"{where}.gxp must be true or false")

    item_schema = base_dir / _string(entry["item_schema"], f"{where}.item_schema")
    if not item_schema.is_file():
        raise ConfigError(f"{where}.item_schema: no such file: {item_schema}")

    site = entry.get("site")
    if site is not None:
        site = _string(site, f"{where}.site")

    return QueueConfig(
        name=_string(entry["name"], f"{where}.name"),
        system=_string(entry["system"], f"{where}.system"),
        task=_string(entry["task"], f"{where}.task"),
        vital_base_risk=entry["vital_base_risk"],
        vital_adjusted_category=category,
        planted_rate_override=rate,
        min_review_seconds=min_review,
        feedback=_choice(entry["feedback"], FeedbackTiming, f"{where}.feedback"),
        item_schema=item_schema,
        gxp=gxp,
        site=site,
    )


def _mapping(data: Any, where: str, required: set[str], optional: set[str]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be a mapping")
    unknown = sorted(str(key) for key in data.keys() - required - optional)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
    missing = sorted(required - data.keys())
    if missing:
        raise ConfigError(f"{where}: missing key(s): {', '.join(missing)}")
    return data


def _list(data: Any, where: str) -> list[Any]:
    if not isinstance(data, list) or not data:
        raise ConfigError(f"{where} must be a non-empty list")
    return data


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} must be a non-empty string (quote numbers and versions)")
    return value


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConfigError(f"{where} must be a number")
    return float(value)


def _choice(value: Any, enum: type, where: str) -> Any:
    try:
        return enum(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum)
        raise ConfigError(f"{where} must be one of: {allowed}") from None


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
