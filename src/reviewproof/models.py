"""The domain model."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from reviewproof.canonical import canonical_json, sha256_hex


class DefectClass(str, Enum):
    WRONG_VALUE = "wrong_value"
    OMISSION = "omission"
    WRONG_CLASSIFICATION = "wrong_classification"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    WRONG_TARGET = "wrong_target"
    POLICY_BREACH = "policy_breach"


class Decision(str, Enum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class EventType(str, Enum):
    CONTEXT_REGISTERED = "context_registered"
    SUBMITTED = "submitted"
    SHOWN = "shown"
    OPENED = "opened"
    EDITED = "edited"
    DECIDED = "decided"
    EXECUTE_REQUESTED = "execute_requested"
    EXECUTED = "executed"
    EXECUTE_FAILED = "execute_failed"
    BLOCKED = "blocked"
    WITHDRAWN = "withdrawn"
    FEEDBACK_SENT = "feedback_sent"
    QUALIFICATION_STARTED = "qualification_started"
    QUALIFICATION_JUDGED = "qualification_judged"
    QUALIFICATION_SHOWN = "qualification_shown"
    QUALIFICATION_ANSWERED = "qualification_answered"
    QUALIFICATION_FINISHED = "qualification_finished"


class CallState(str, Enum):
    """Where the real action behind a submission key stands (ADR-005)."""

    WAITING = "waiting"
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class ItemKind(str, Enum):
    """Whether an item is real or planted. Never part of anything the host receives."""

    REAL = "real"
    PLANTED = "planted"


class Purpose(str, Enum):
    """What a planted error is for. The two pools never mix."""

    LIVE = "live"
    QUALIFICATION = "qualification"


class QualificationStatus(str, Enum):
    NOT_QUALIFIED = "not_qualified"
    QUALIFIED = "qualified"
    REQUALIFICATION_REQUIRED = "requalification_required"


class FeedbackTiming(str, Enum):
    IMMEDIATE = "immediate"
    BATCHED = "batched"


class RiskCategory(str, Enum):
    """VITAL risk-adjusted category (ADR-007)."""

    LOW = "low"
    MEDIUM = "medium"
    #: VITAL's word for ``MEDIUM``, which is what is stored.
    MODERATE = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"
    CRITICAL = "critical"


def new_token() -> str:
    """An opaque random token. Real and planted items get tokens from this same function."""
    return secrets.token_urlsafe(32)


def derive_token(seed: bytes, item_id: str) -> str:
    """A keyed submission's token, which can be rebuilt (ADR-005). It looks like any other."""
    digest = hmac.new(seed, item_id.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_item_id() -> str:
    """A random item ID. It carries no order, time or kind."""
    return secrets.token_hex(16)


@dataclass(frozen=True)
class CallStatus:
    """The state of one proposed action. For server code only: never show it to a reviewer."""

    key: str
    state: CallState


@dataclass(frozen=True)
class Item:
    """A real AI proposal that the host submits for review."""

    # Kept out of repr so that logging an Item cannot leak clinical or patient data.
    payload: Mapping[str, Any] = field(repr=False)
    target_id: str
    trace: Sequence[Mapping[str, Any]] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        if not isinstance(self.target_id, str) or not self.target_id.strip():
            raise ValueError("target_id must be a non-empty string")


@dataclass(frozen=True)
class Ticket:
    """All the host gets back for an item to review: an opaque token and what to render.

    Adding a field here changes what the host can see. Real and planted tickets must stay
    indistinguishable, and a ticket must never carry a target ID.
    """

    token: str = field(repr=False)
    payload: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True)
class OversightContext:
    """The AI system version an item came from. The task is set per queue."""

    system: str
    model_id: str
    model_version: str
    prompt_version: str
    tool_schema_hash: str
    intended_use: str = ""

    def __post_init__(self) -> None:
        for name in ("system", "model_id", "model_version", "prompt_version", "tool_schema_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.intended_use, str):
            raise TypeError("intended_use must be a string")

    @property
    def fingerprint(self) -> str:
        """Identifies the context. A change to the intended-use text does not change it."""
        identity = [
            self.system,
            self.model_id,
            self.model_version,
            self.prompt_version,
            self.tool_schema_hash,
        ]
        return sha256_hex(canonical_json(identity))
