"""Adapters for plain Python functions.

``run_review`` suits an approval function that answers while you wait. ``gate`` wraps a function
that makes proposals, when reviews happen later.
"""

from __future__ import annotations

import copy
import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from reviewproof.errors import OversightError
from reviewproof.guard import ExecutionResult, Executor
from reviewproof.models import Decision, Item, Ticket

if TYPE_CHECKING:
    from reviewproof.api import Oversight


@dataclass(frozen=True)
class Reviewed:
    ticket: Ticket = field(repr=False)
    decision: Decision
    #: None for a rejected item.
    execution: ExecutionResult | None = None


def run_review(
    oversight: Oversight,
    item: Item,
    reviewer: str,
    ask: Callable[[dict[str, Any]], Any],
    executor: Executor,
    context: Mapping[str, Any] | None = None,
) -> list[Reviewed]:
    """Submit an item and take everything that comes back through review and execution.

    ``ask(payload)`` is your approval step. It returns ``"approve"``, ``"reject"``, or
    ``("edit", the_whole_edited_item)``. It is sometimes asked twice for one submission, and
    must not be able to tell which question is which. **Do not show the reviewer where one
    submission ends and the next begins.** If prompts appear one at a time with pauses between
    submissions, two in quick succession stand out; use a queue instead.

    If one item fails, the others are still processed, then the first error is raised.
    """
    results: list[Reviewed] = []
    failure: Exception | None = None
    for ticket in oversight.submit(item, reviewer):
        try:
            oversight.shown(ticket.token, reviewer, context)
            decision, final = _verdict(ask(copy.deepcopy(dict(ticket.payload))))
            oversight.decided(ticket.token, reviewer, decision, final_payload=final)
            execution = None
            if decision is not Decision.REJECT:
                approved = ticket.payload if final is None else final
                execution = oversight.execute(ticket.token, approved, executor)
            results.append(Reviewed(ticket, decision, execution))
        except Exception as error:
            failure = failure or error
    if failure is not None:
        raise failure
    return results


def gate(
    oversight: Oversight,
    *,
    enqueue: Callable[[Ticket], None],
    target_id: Callable[..., str],
    reviewer: Callable[..., str | None] | None = None,
) -> Callable[[Callable[..., Mapping[str, Any]]], Callable[..., None]]:
    """Decorate a function that returns a proposal, so that each result goes to review.

    ``target_id`` and ``reviewer`` are called with the same arguments as the function.
    ``enqueue`` receives every ticket, one or two per call. The decorated function returns
    nothing: act on proposals only through ``execute()``.
    """

    def decorate(propose: Callable[..., Mapping[str, Any]]) -> Callable[..., None]:
        @functools.wraps(propose)
        def wrapper(*args: Any, **kwargs: Any) -> None:
            proposal = propose(*args, **kwargs)
            item = Item(payload=proposal, target_id=target_id(*args, **kwargs))
            who = None if reviewer is None else reviewer(*args, **kwargs)
            for ticket in oversight.submit(item, who):
                enqueue(ticket)

        return wrapper

    return decorate


def _verdict(answer: Any) -> tuple[Decision, Mapping[str, Any] | None]:
    if isinstance(answer, tuple) and len(answer) == 2 and answer[0] == Decision.EDIT.value:
        return Decision.EDIT, answer[1]
    if answer in (Decision.APPROVE.value, Decision.REJECT.value):
        return Decision(answer), None
    raise OversightError('ask() must return "approve", "reject" or ("edit", edited_item)')
