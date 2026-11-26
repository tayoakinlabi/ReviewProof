"""The errors the host-facing API raises."""

from __future__ import annotations


class OversightError(ValueError):
    """The request was refused. The message says why."""


class UnknownTokenError(OversightError):
    """No item in this queue has that token."""


class TransitionError(OversightError):
    """The item is not at a stage where this can happen."""


class GuardError(OversightError):
    """A safety check failed. **Nothing was executed.**"""


class UnrecordedExecutionError(RuntimeError):
    """**The executor ran**, but the result could not be written to the log. Do not run it again.

    Deliberately not an ``OversightError``: those mean that nothing happened.
    """
