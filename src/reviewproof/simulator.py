"""Simulated reviewers, for demonstrations and for testing the software (step 2.8).

**Their behaviour is assumed, not observed.** A simulation shows that ReviewProof measures what
it is told happened. It says nothing about how real people review, and its numbers must never be
presented as results.

The simulator drives the real API with a simulated clock. Unlike a real application it knows
which items are planted, because it has to decide how a reviewer of each kind would react.
"""

from __future__ import annotations

import copy
import math
import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from reviewproof.api import Oversight
from reviewproof.canonical import canonical_json, format_timestamp
from reviewproof.guard import Executor
from reviewproof.models import Decision, Item
from reviewproof.planted import PlantedError


@dataclass(frozen=True)
class Profile:
    """How a kind of reviewer behaves."""

    name: str
    #: Review times follow a lognormal distribution: most near the median, a few much longer.
    median_seconds: float
    spread: float
    catch_probability: float
    #: What happens to real proposals that are not simply approved.
    reject_real: float = 0.0
    edit_real: float = 0.0
    #: Lost from the catch probability with each item further into a session.
    tiring_per_item: float = 0.0


CAREFUL = Profile(
    "careful",
    median_seconds=90,
    spread=0.4,
    catch_probability=0.90,
    reject_real=0.03,
    edit_real=0.05,
)
RUBBER_STAMP = Profile(
    "rubber-stamp", median_seconds=8, spread=0.5, catch_probability=0.30, edit_real=0.005
)


class SimulatedClock:
    """The time ReviewProof records. It only moves when told to."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        self.now = start.astimezone(timezone.utc)

    def __call__(self) -> str:
        return format_timestamp(self.now)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("the clock cannot go backwards")
        self.now += timedelta(seconds=seconds)

    def move_to(self, moment: datetime) -> None:
        self.advance((moment - self.now).total_seconds())


class SimulatedReviewer:
    def __init__(
        self,
        reviewer_id: str,
        profile: Profile,
        planted: Iterable[PlantedError],
        rng: random.Random,
        *,
        reword: Iterable[str] | None = None,
    ) -> None:
        """``reword`` names the properties that a simulated edit to a **real** proposal may
        change: the longest text among them gets a few more words. Without it, the longest text
        of the whole item does. Name free-text properties only: the added words could break a
        list of allowed values or a pattern, and the item format is not checked. Planted errors
        are judged the same either way.
        """
        if reword is not None:
            if isinstance(reword, str):
                raise ValueError("reword must be a list of property names, not one string")
            reword = frozenset(reword)
            if not reword or not all(isinstance(name, str) and name for name in reword):
                raise ValueError("reword must name at least one property")
        self._reword = reword
        self.reviewer_id = reviewer_id
        self.profile = profile
        self._rng = rng
        self._planted = {canonical_json(dict(p.payload)): p for p in planted}

    def seconds(self) -> float:
        return self._rng.lognormvariate(math.log(self.profile.median_seconds), self.profile.spread)

    def judge(
        self, payload: Mapping[str, Any], position: int = 0
    ) -> tuple[Decision, dict[str, Any] | None]:
        """The decision, and the edited item for an edit. ``position`` counts from 0 within a
        session.
        """
        profile = self.profile
        planted = self._planted.get(canonical_json(dict(payload)))
        if planted is not None:
            chance = max(0.0, profile.catch_probability - profile.tiring_per_item * position)
            if self._rng.random() >= chance:
                return Decision.APPROVE, None
            if planted.expected_decision != "reject" and planted.has_correct_value:
                corrected = {**payload, planted.defect_field: planted.correct_value}
                return Decision.EDIT, copy.deepcopy(corrected)
            return Decision.REJECT, None
        draw = self._rng.random()
        if draw < profile.reject_real:
            return Decision.REJECT, None
        if draw < profile.reject_real + profile.edit_real:
            return Decision.EDIT, _reworded(payload, self._reword)
        return Decision.APPROVE, None


def _reworded(payload: Mapping[str, Any], among: frozenset[str] | None = None) -> dict[str, Any]:
    """A small correction to a real proposal: the longest piece of text is changed, or the
    longest among the properties named in ``among``.
    """
    edited = copy.deepcopy(dict(payload))
    texts = [k for k, v in edited.items() if isinstance(v, str) and (among is None or k in among)]
    if not texts:
        named = "" if among is None else " in any property named by reword"
        raise ValueError(f"the item has no text to edit{named}")
    field = max(texts, key=lambda k: len(edited[k]))
    edited[field] += " (reworded)"
    return edited


def run_session(
    oversight: Oversight,
    reviewer: SimulatedReviewer,
    items: Iterable[Item],
    clock: SimulatedClock,
    executor: Executor,
    pause_seconds: tuple[float, float] = (2.0, 10.0),
    rng: random.Random | None = None,
) -> int:
    """One sitting: submit each item, then review and execute whatever comes back.

    Returns how many items the reviewer decided. ``clock`` must be the clock the ``Oversight``
    was opened with.
    """
    pauses = rng or random.Random(0)
    decided = 0
    for item in items:
        for ticket in oversight.submit(item, reviewer.reviewer_id):
            clock.advance(pauses.uniform(*pause_seconds))
            oversight.shown(ticket.token, reviewer.reviewer_id)
            clock.advance(reviewer.seconds())
            decision, final = reviewer.judge(ticket.payload, decided)
            oversight.decided(ticket.token, reviewer.reviewer_id, decision, final_payload=final)
            decided += 1
            if decision is not Decision.REJECT:
                approved = ticket.payload if final is None else final
                oversight.execute(ticket.token, approved, executor)
    return decided
