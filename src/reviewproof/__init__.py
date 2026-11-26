"""ReviewProof: a rubber-stamp detector for AI oversight."""

from reviewproof.api import Oversight, initialise
from reviewproof.errors import (
    GuardError,
    OversightError,
    TransitionError,
    UnknownTokenError,
    UnrecordedExecutionError,
)
from reviewproof.feedback import Feedback, Outcome
from reviewproof.guard import ExecutionResult
from reviewproof.models import Decision, Item, Ticket

__version__ = "0.0.0"

__all__ = [
    "Decision",
    "ExecutionResult",
    "Feedback",
    "GuardError",
    "Item",
    "Outcome",
    "Oversight",
    "OversightError",
    "Ticket",
    "TransitionError",
    "UnknownTokenError",
    "UnrecordedExecutionError",
    "__version__",
    "initialise",
]
