"""Pydantic AI (ADR-005).

A run pauses with every call that needs approval, and **all of them must be answered
together**, so a run resumes once everything it waits for has been decided. After each run,
keep only the newest messages and pause: resuming an older one runs finished calls again, which
ReviewProof refuses.

**Any failure of a reviewed tool raises ``RunStopped``,** which is deliberately not an
``Exception``. Pydantic AI turns several errors into a message for the model, which then tries
again: its own retry errors, and any ``ValidationError``, such as one from checking your target
system's reply after the write. A hook that recovers from errors can do the same to any other
``Exception``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from typing import Any

from reviewproof.adapters.tool import NOT_APPROVED, ReviewedTool, reply_text
from reviewproof.errors import OversightError
from reviewproof.models import CallState

try:
    from pydantic_ai import DeferredToolRequests, DeferredToolResults, Tool, ToolDenied
except ImportError as error:  # pragma: no cover - needs an install without Pydantic AI
    raise ImportError(
        "the Pydantic AI adapter needs Pydantic AI, and Python 3.11 or later: "
        'pip install "reviewproof[pydantic-ai]"'
    ) from error


class RunStopped(BaseException):
    """A reviewed tool failed, and the agent's run must end. ``__cause__`` says why.

    **``except Exception`` does not catch this.** Catch it by name where you run the agent.
    After it, do not resume: check the target system, because the write may have happened, and
    so may others approved in the same run.
    """


class ReviewTools:
    """The reviewed tools of an agent.

    ``tools`` maps each tool name to its ``ReviewedTool``. ``run_id(deps)`` returns the ID of
    the run from the dependencies you pass to ``agent.run``. Give the same ID to ``submit()``
    and ``decisions()``.
    """

    def __init__(self, tools: Mapping[str, ReviewedTool], *, run_id: Callable[[Any], str]) -> None:
        if not isinstance(tools, Mapping) or not tools:
            raise OversightError("tools must map each tool name to a ReviewedTool")
        for name, tool in tools.items():
            if not isinstance(name, str) or not isinstance(tool, ReviewedTool):
                raise OversightError("tools must map each tool name to a ReviewedTool")
        if not callable(run_id):
            raise OversightError("run_id must be a function of the run's dependencies")
        self._tools = dict(tools)
        self._run_id = run_id

    def tool(self, name: str, *, description: str, parameters_json_schema: dict[str, Any]) -> Tool:
        """The tool to give your agent. It needs approval, and has no time limit."""
        if name not in self._tools:
            raise OversightError("there is no reviewed tool of that name")
        reviewed = self._tools[name]

        # The context is positional only, so that a tool argument may have any name.
        def call(context: Any, /, **arguments: Any) -> str:
            try:
                _require_no_time_limit(context)
                key = _key(self._run_id(context.deps), context.tool_call_id)
                return reply_text(reviewed.run(key, arguments).reply)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as error:
                # Raised as it is, some errors would reach the model, which would try again.
                raise RunStopped(f"a reviewed tool failed ({type(error).__name__})") from error

        tool = Tool.from_schema(
            call,
            name=name,
            description=description,
            json_schema=parameters_json_schema,
            takes_ctx=True,
        )
        tool.requires_approval = True
        # A tool's own limit comes before the agent's. With a limit, the model would be told
        # that the call timed out while the write went on.
        tool.timeout = math.inf
        return tool

    def submit(self, requests: DeferredToolRequests, run_id: str) -> None:
        """Send every call to a reviewed tool that the run waits on to review. Safe to repeat.

        Call it whenever a run's output is ``DeferredToolRequests``. Other calls are left to
        you.
        """
        calls = [
            (part.tool_name, _key(run_id, part.tool_call_id), _arguments(part))
            for part in requests.approvals
            if part.tool_name in self._tools
        ]
        # All checked before any is submitted.
        for name, key, arguments in calls:
            self._tools[name].submit(key, arguments)

    def decisions(self, requests: DeferredToolRequests, run_id: str) -> DeferredToolResults | None:
        """The answers for the reviewed calls in ``requests``, once **all** have been decided.
        Until then, and if there are none, None.

        If the run also waits on calls to other tools, add your answers for them to the
        result's ``approvals`` and ``calls`` before you resume.
        """
        approvals: dict[str, Any] = {}
        for part in requests.approvals:
            if part.tool_name not in self._tools:
                continue
            decided = self._tools[part.tool_name].state(_key(run_id, part.tool_call_id))
            if decided is CallState.WAITING:
                return None
            approvals[part.tool_call_id] = (
                ToolDenied(NOT_APPROVED) if decided is CallState.REJECT else True
            )
        return DeferredToolResults(approvals=approvals) if approvals else None


def _require_no_time_limit(context: Any) -> None:
    """Read the limit that is in force now: another setting may have replaced ours."""
    limit = context.tool_manager.tools[context.tool_name].tool_def.timeout
    if limit != math.inf:
        raise OversightError(
            "this reviewed tool has been given a time limit. The model would be told it failed "
            "while the write went on. Nothing was executed."
        )


def _key(run_id: Any, call_id: Any) -> str:
    for what, value in (("run ID", run_id), ("tool-call ID", call_id)):
        if not isinstance(value, str) or not value:
            raise OversightError(f"the {what} must be text")
    # As a JSON list, so that no two pairs can spell the same key.
    return json.dumps([run_id, call_id])


def _arguments(part: Any) -> dict[str, Any]:
    try:
        return part.args_as_dict(raise_if_invalid=True)
    except Exception as error:
        raise OversightError("the model's tool arguments are not a JSON object") from error
