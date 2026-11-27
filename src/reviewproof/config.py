"""Systems and queues, loaded from a YAML file.

Loading is strict: an unknown or repeated key is an error, so a typing mistake cannot silently
fall back to a default.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from reviewproof.models import FeedbackTiming, OversightContext, RiskCategory
from reviewproof.strictload import LoadError, load_file

#: Planted share of all items shown, by risk tier. Proposed defaults, to be calibrated in a pilot.
TIER_RATES: tuple[float, ...] = (0.01, 0.02, 0.03)

#: Above this, a planted error would be needed for more than every real item.
MAX_PLANTED_RATE = 0.5

DEFAULT_RESERVED_TARGET_PREFIX = "rp-reserved-"
MIN_RESERVED_PREFIX_LENGTH = 6

#: VITAL's ladder, A1 to A10, with a half step between neighbours (ADR-007).
_BASE_RISK = re.compile(r"A(?:10|[1-9](?:\.5)?)")

#: No individual score is ever shown from fewer planted errors than this.
INDIVIDUAL_FLOOR = 10


class ConfigError(ValueError):
    """The configuration is invalid. The message says where."""


def risk_tier(base_risk: str, adjusted_category: RiskCategory | None = None) -> int:
    """Return the tier (0, 1 or 2) for a VITAL base risk such as ``A5`` or ``A5.5``.

    A1-A4 is tier 0, A5-A6 tier 1 and A7-A10 tier 2. A half step takes the next whole step's
    tier. A High risk-adjusted category moves a queue up one tier, and the top tier stays where
    it is. Very High and Critical take the top tier.
    """
    if not isinstance(base_risk, str) or not _BASE_RISK.fullmatch(base_risk):
        raise ConfigError(
            "vital_base_risk must be from A1 to A10, or a half step such as A5.5, "
            f"not {base_risk!r}"
        )
    level = math.ceil(float(base_risk[1:]))
    top = len(TIER_RATES) - 1
    if adjusted_category in (RiskCategory.VERY_HIGH, RiskCategory.CRITICAL):
        return top
    tier = 0 if level <= 4 else 1 if level <= 6 else 2
    if adjusted_category is RiskCategory.HIGH:
        tier += 1
    return min(tier, top)


@dataclass(frozen=True)
class Thresholds:
    """When a report or alert says something needs attention. Shares are 0 to 1.

    These are proposed defaults, to be calibrated in a pilot.
    """

    #: Alert when the rolling catch rate is below this.
    catch_rate_min: float = 0.80
    #: Alert when any defect class is caught less often than this.
    class_catch_rate_min: float = 0.60
    #: Alert when more than this share of a week's approvals were faster than the minimum.
    fast_approval_share_max: float = 0.20
    #: Alert when agreement is above this while the catch rate is falling.
    agreement_rate_max: float = 0.98
    #: Alert when the catch rate late in sessions is this much lower than early on.
    fatigue_drop_max: float = 0.20
    #: Fewest planted errors (or approvals, for weekly speed) before a rule is judged.
    min_sample: int = 10
    #: The rolling catch rate covers this many of the latest planted errors.
    rolling_window: int = 50
    #: A longer pause between one reviewer's decisions starts a new session.
    session_gap_minutes: int = 30
    individual_min_planted: int = INDIVIDUAL_FLOOR
    #: Optional cap on the items one reviewer decides in a day.
    max_items_per_reviewer_per_day: int | None = None


_SHARES = (
    "catch_rate_min",
    "class_catch_rate_min",
    "fast_approval_share_max",
    "agreement_rate_max",
    "fatigue_drop_max",
)
_COUNTS = ("min_sample", "rolling_window", "session_gap_minutes", "individual_min_planted")


@dataclass(frozen=True)
class Smtp:
    host: str
    sender: str
    port: int = 587
    starttls: bool = True
    #: Names of environment variables, so that no password is ever kept in the settings file.
    username_env: str | None = None
    password_env: str | None = None


@dataclass(frozen=True)
class AlertRoute:
    """Where alerts go: to the QA lead. There is deliberately no other kind of recipient."""

    emails: tuple[str, ...] = ()
    webhook: str | None = None
    smtp: Smtp | None = None


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
    thresholds: Thresholds = Thresholds()

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
    reporting: Thresholds = Thresholds()
    alerts: AlertRoute = AlertRoute()


def load_config(path: str | Path) -> Config:
    """Load and validate a configuration file. Relative paths in it are relative to the file."""
    path = Path(path)
    try:
        data = load_file(path)
    except LoadError as error:
        raise ConfigError(str(error)) from error
    return parse_config(data, base_dir=path.parent)


def parse_config(data: Any, base_dir: Path) -> Config:
    root = _mapping(
        data,
        "config",
        required={"systems", "queues"},
        optional={"reserved_target_prefix", "reporting", "alerts"},
    )
    reporting = _parse_thresholds(root.get("reporting", {}), "reporting", Thresholds())
    alerts = _parse_alerts(root.get("alerts", {}))

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
        queue = _parse_queue(entry, f"queues[{index}]", base_dir, reporting)
        if queue.name in queues:
            raise ConfigError(f"queues[{index}]: duplicate queue name {queue.name!r}")
        if queue.system not in systems:
            raise ConfigError(f"queues[{index}]: unknown system {queue.system!r}")
        queues[queue.name] = queue

    return Config(MappingProxyType(systems), MappingProxyType(queues), prefix, reporting, alerts)


def _parse_system(data: Any, where: str) -> OversightContext:
    required = ("name", "model_id", "model_version", "prompt_version", "tool_schema_hash")
    entry = _mapping(data, where, required=set(required), optional={"intended_use"})
    # Versions must be quoted in YAML: unquoted, 1.10 would be read as the number 1.1.
    values = {key: _string(entry[key], f"{where}.{key}") for key in required}
    intended_use = entry.get("intended_use", "")
    if not isinstance(intended_use, str):
        raise ConfigError(f"{where}.intended_use must be a string")
    return OversightContext(system=values.pop("name"), intended_use=intended_use, **values)


def _parse_queue(data: Any, where: str, base_dir: Path, defaults: Thresholds) -> QueueConfig:
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
        optional={"vital_adjusted_category", "planted_rate", "gxp", "site", "thresholds"},
    )
    thresholds = _parse_thresholds(entry.get("thresholds", {}), f"{where}.thresholds", defaults)

    category = entry.get("vital_adjusted_category")
    if category is not None:
        category = _risk_category(category, f"{where}.vital_adjusted_category")

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
        thresholds=thresholds,
    )


def _parse_thresholds(data: Any, where: str, defaults: Thresholds) -> Thresholds:
    names = {*_SHARES, *_COUNTS, "max_items_per_reviewer_per_day"}
    entry = _mapping(data, where, required=set(), optional=names)
    values: dict[str, Any] = {}
    for name, value in entry.items():
        label = f"{where}.{name}"
        if name in _SHARES:
            values[name] = _number(value, label)
            if not 0 <= values[name] <= 1:
                raise ConfigError(f"{label} must be a share from 0 to 1 (0.8 means 80%)")
        elif name == "max_items_per_reviewer_per_day" and value is None:
            values[name] = None
        elif type(value) is not int or value < 1:
            raise ConfigError(f"{label} must be a whole number, 1 or more")
        else:
            values[name] = value
    result = replace(defaults, **values)
    if result.individual_min_planted < INDIVIDUAL_FLOOR:
        raise ConfigError(
            f"{where}.individual_min_planted cannot be below {INDIVIDUAL_FLOOR}: no individual "
            "score is shown from fewer planted errors"
        )
    return result


def _parse_alerts(data: Any) -> AlertRoute:
    entry = _mapping(data, "alerts", required=set(), optional={"qa_lead", "smtp"})
    lead = _mapping(
        entry.get("qa_lead", {}), "alerts.qa_lead", required=set(), optional={"email", "webhook"}
    )
    emails = lead.get("email", [])
    if not isinstance(emails, list):
        raise ConfigError("alerts.qa_lead.email must be a list of addresses")
    for address in emails:
        _address(address, "alerts.qa_lead.email")

    webhook = lead.get("webhook")
    if webhook is not None:
        parts = urlsplit(_string(webhook, "alerts.qa_lead.webhook"))
        local = parts.hostname in ("localhost", "127.0.0.1", "::1")
        if not parts.hostname or not (
            parts.scheme == "https" or (parts.scheme == "http" and local)
        ):
            raise ConfigError("alerts.qa_lead.webhook must be an https address")

    smtp = None
    if "smtp" in entry:
        smtp = _parse_smtp(entry["smtp"])
    if emails and smtp is None:
        raise ConfigError("alerts.smtp is needed to send email")
    return AlertRoute(tuple(emails), webhook, smtp)


def _parse_smtp(data: Any) -> Smtp:
    entry = _mapping(
        data,
        "alerts.smtp",
        required={"host", "sender"},
        optional={"port", "starttls", "username_env", "password_env"},
    )
    port = entry.get("port", 587)
    if type(port) is not int or not 0 < port < 65536:
        raise ConfigError("alerts.smtp.port must be a port number")
    starttls = entry.get("starttls", True)
    if not isinstance(starttls, bool):
        raise ConfigError("alerts.smtp.starttls must be true or false")
    names = {}
    for key in ("username_env", "password_env"):
        if key in entry:
            names[key] = _string(entry[key], f"alerts.smtp.{key}")
    if len(names) == 1:
        raise ConfigError("alerts.smtp needs both username_env and password_env, or neither")
    if names and not starttls:
        raise ConfigError("alerts.smtp: a password is only sent with starttls: true")
    return Smtp(
        host=_string(entry["host"], "alerts.smtp.host"),
        sender=_address(entry["sender"], "alerts.smtp.sender"),
        port=port,
        starttls=starttls,
        **names,
    )


def _address(value: Any, where: str) -> str:
    # Line breaks would let a value add headers to the message.
    if not isinstance(value, str) or not re.fullmatch(r"[^@\s,<>]+@[^@\s,<>]+", value):
        raise ConfigError(f"{where}: {value!r} is not an email address")
    return value


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


def _risk_category(value: Any, where: str) -> RiskCategory:
    if value == "moderate":
        return RiskCategory.MODERATE
    try:
        return RiskCategory(value)
    except ValueError:
        # VITAL's own words, with "medium" as another spelling of "moderate".
        raise ConfigError(
            f"{where} must be one of: low, moderate (or medium), high, very_high, critical"
        ) from None


def _choice(value: Any, enum: type, where: str) -> Any:
    try:
        return enum(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum)
        raise ConfigError(f"{where} must be one of: {allowed}") from None
