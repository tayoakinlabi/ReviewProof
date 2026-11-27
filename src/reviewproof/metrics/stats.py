"""Rates with their uncertainty."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

#: The 97.5th percentile of the standard normal distribution, for 95% intervals.
Z_95 = 1.959963984540054


def wilson(successes: int, total: int, z: float = Z_95) -> tuple[float, float] | None:
    """The Wilson score interval for a proportion, or None when there is nothing to measure.

    Unlike the simple "rate plus or minus", it behaves sensibly for small samples and for rates
    near 0% or 100%.
    """
    if total == 0:
        return None
    if not 0 <= successes <= total:
        raise ValueError("successes must be between 0 and total")
    share = successes / total
    scale = 1 + z * z / total
    centre = (share + z * z / (2 * total)) / scale
    margin = z * sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / scale
    low = 0.0 if successes == 0 else max(0.0, centre - margin)
    high = 1.0 if successes == total else min(1.0, centre + margin)
    return low, high


@dataclass(frozen=True)
class Rate:
    """So many out of so many."""

    successes: int
    total: int
    #: Held back to protect one reviewer. The counts are then zero: they are not kept.
    withheld: bool = False

    @classmethod
    def of(cls, outcomes: list[bool]) -> Rate:
        return cls(sum(outcomes), len(outcomes))

    @classmethod
    def hidden(cls) -> Rate:
        return cls(0, 0, withheld=True)

    @property
    def value(self) -> float | None:
        return None if self.total == 0 else self.successes / self.total

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson(self.successes, self.total)
