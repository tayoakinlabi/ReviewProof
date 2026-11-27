"""What every adapter for an agent library shares (ADR-005).

An agent library pauses before a tool runs and resumes it later. ``ReviewedTool`` is the part
that does not depend on which library: ``submit`` before the pause, ``run`` after it.

**The library's approval only wakes the call.** ``run`` writes nothing unless ReviewProof has a
recorded approval or edit of exactly this content, and never writes the same call twice, so a
forged or repeated resume, a replayed checkpoint or a standing approval cannot get round review.
That protects only writes made through here: never give the agent your write function itself.

Nothing here returns a ticket, a token or planted content to its caller. Tickets go to
``enqueue`` and nowhere else.
"""

from __future__ import annotations

import copy
import inspect
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from reviewproof.canonical import canonical_json
from reviewproof.errors import GuardError, OversightError, TransitionError
from reviewproof.models import CallState, Item, Ticket
from reviewproof.records import plain_json

if TYPE_CHECKING:
    from reviewproof.api import Oversight

#: What the agent is told about a rejected action. It never says more.
NOT_APPROVED = "A reviewer did not approve this action. It was not done."
EDITED_NOTE = (
    "Done, with changes a reviewer made to: {fields}. What was written differs from your proposal."
)


class ReviewPending(TransitionError):
    """The call was resumed before the real action was decided. Nothing ran."""


@dataclass(frozen=True)
class ToolOutcome:
    #: False only for a rejected action.
    executed: bool
    #: What to give the agent as the tool's result.
    reply: Any = field(repr=False)
    #: The arguments a reviewer changed, if any.
    edited_fields: tuple[str, ...] = ()


class ReviewedTool:
    """One write tool of an agent, behind review.

    ``write`` is your synchronous function. It takes the tool's arguments by name: the one
    named by ``target_argument`` says what is written to, and all the others together are the
    item that is reviewed, in the format of the queue's item schema. As with any executor, make
    your target system reject the reserved target range.

    ``open_oversight`` returns a new ``Oversight`` each time. Libraries may run tools on other
    threads, and an instance belongs to one thread.

    ``enqueue(ticket)`` puts a ticket in your review queue. It is called for every ticket on
    **every** ``submit``, including repeats, so it must ignore a token it already has.

    ``reviewer(key)``, if given, names who will review, and must give the same answer each time.

    ``approved_payload(key, proposal)`` returns the item as the reviewer edited it, from your
    review application. It is called only after an edit. Without it, an edited action is
    refused and nothing runs.
    """

    def __init__(
        self,
        open_oversight: Callable[[], AbstractContextManager[Oversight]],
        write: Callable[..., Any],
        *,
        target_argument: str,
        enqueue: Callable[[Ticket], None],
        reviewer: Callable[[str], str | None] | None = None,
        approved_payload: Callable[[str, dict[str, Any]], Mapping[str, Any]] | None = None,
    ) -> None:
        for name, value in (
            ("open_oversight", open_oversight),
            ("write", write),
            ("enqueue", enqueue),
        ):
            if not callable(value):
                raise OversightError(f"{name} must be a function")
        if _is_async(write):
            raise OversightError("write must be a synchronous function")
        if not isinstance(target_argument, str) or not target_argument:
            raise OversightError("target_argument must name one of the tool's arguments")
        self._open = open_oversight
        self._write = write
        self._target_argument = target_argument
        self._enqueue = enqueue
        self._reviewer = reviewer
        self._approved_payload = approved_payload

    def submit(self, key: str, arguments: Mapping[str, Any]) -> None:
        """Before the pause: send the proposed call to review. Safe to run again.

        Returns nothing, so that no ticket can end up in the agent's state.
        """
        item = self._item(arguments)
        with self._open() as oversight:
            tickets = oversight.submit(item, self._reviewer_for(key), key=key)
        for ticket in tickets:
            self._enqueue(ticket)

    def state(self, key: str) -> CallState:
        """Whether the real action is still waiting, or how it was decided. Server code only."""
        with self._open() as oversight:
            return oversight.call_status(key).state

    def run(self, key: str, arguments: Mapping[str, Any]) -> ToolOutcome:
        """After the pause: do what was decided. ``arguments`` are the call as the library
        replays it.

        **Let every exception reach your application. Never turn one into a tool result:** the
        agent could try again, and a write that failed may have happened in part.
        """
        item = self._item(arguments)
        proposal = dict(item.payload)
        with self._open() as oversight:
            # Fails for a call that was never submitted, such as one that skipped the pause.
            state = oversight.call_status(key).state
            # The library may hand back something other than what went to review. Repeating the
            # submission checks target, content and reviewer, and records nothing.
            oversight.submit(item, self._reviewer_for(key), key=key)
            if state is CallState.WAITING:
                raise ReviewPending("this action has not been decided yet. Nothing was executed.")
            if state is CallState.REJECT:
                return ToolOutcome(executed=False, reply=NOT_APPROVED)
            final = proposal
            if state is CallState.EDIT and self._approved_payload is not None:
                final = self._edited(key, proposal)
            result = oversight.execute_call(key, final, self._execute)
        if not result.executed:
            raise GuardError("The action was not executed.")
        if state is not CallState.EDIT:
            return ToolOutcome(executed=True, reply=result.value)
        changed = _changed(proposal, final)
        note = EDITED_NOTE.format(fields=", ".join(changed))
        return ToolOutcome(True, {"note": note, "result": result.value}, changed)

    # ------------------------------------------------------------- parts

    def _item(self, arguments: Mapping[str, Any]) -> Item:
        if not isinstance(arguments, Mapping):
            raise OversightError("the tool's arguments must be a mapping")
        name = self._target_argument
        target = arguments.get(name)
        if not isinstance(target, str) or not target:
            raise OversightError(f"the tool call needs the text argument {name!r}")
        rest = {key: value for key, value in arguments.items() if key != name}
        return Item(payload=plain_json(rest, "the tool's arguments"), target_id=target)

    def _edited(self, key: str, proposal: dict[str, Any]) -> dict[str, Any]:
        final = plain_json(
            self._approved_payload(key, copy.deepcopy(proposal)), "approved_payload()"
        )
        if self._target_argument in final:
            raise OversightError("an edit cannot change what is written to")
        return final

    def _reviewer_for(self, key: str) -> str | None:
        return None if self._reviewer is None else self._reviewer(key)

    def _execute(self, target_id: str, payload: dict[str, Any]) -> Any:
        value = self._write(**{**payload, self._target_argument: target_id})
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise TypeError("write returned something to await, so it may not have run")
        return value


def reply_text(reply: Any) -> str:
    """A tool result as text, for a library that wants text. It must not fail: by the time it
    is called, the write has happened.
    """
    if isinstance(reply, str):
        return reply
    try:
        return json.dumps(reply, default=str, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        return str(reply)


def _is_async(function: Callable[..., Any]) -> bool:
    return inspect.iscoroutinefunction(function) or inspect.iscoroutinefunction(
        getattr(function, "__call__", None)  # noqa: B004 - a callable object's own method
    )


def _changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[str, ...]:
    """Top-level fields that differ, compared as JSON, so that 1 and true are different."""

    def text(values: Mapping[str, Any], name: str) -> str | None:
        return canonical_json([values[name]]) if name in values else None

    return tuple(sorted(n for n in {*before, *after} if text(before, n) != text(after, n)))
