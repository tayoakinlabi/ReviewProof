"""OpenAI Agents SDK (ADR-005).

The SDK pauses a run for each call to a tool that needs approval, saves each finished call's
result in the run's state, and resumes from that state. So **always save, and resume from, the
newest state.** An older one makes the SDK run finished calls again; ReviewProof refuses the
second write, and the run breaks.

``ReviewTools`` builds the function tools itself, because the settings that matter are on the
tool: an error from one of these stops the run. **Do not give them a time limit or an output
guardrail.** With either, the model can be told a call failed or was blocked although the
write happened, or is still going on.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from typing import Any

from reviewproof.adapters.tool import NOT_APPROVED, ReviewedTool, reply_text
from reviewproof.errors import OversightError
from reviewproof.models import CallState

try:
    from agents import FunctionTool
except ImportError as error:  # pragma: no cover - needs an install without the SDK
    raise ImportError(
        'the OpenAI Agents SDK adapter needs the SDK: pip install "reviewproof[openai-agents]"'
    ) from error


class ToolStopped(RuntimeError):
    """A write was cancelled from inside. It may have happened in part."""


class ReviewTools:
    """The reviewed tools of an agent.

    ``tools`` maps each tool name to its ``ReviewedTool``. ``run_id(context)`` returns the ID
    of the run from the context you pass to ``Runner.run``. It must be the same after the state
    is saved and restored: the SDK may then give you the context as a plain dictionary.

    A run goes: ``Runner.run``, then ``submit(result)``, then save ``result.to_state()``. After
    decisions, restore that state, call ``apply_decisions(state, run_id)`` and, if it returns
    True, run the state and start over with its result.
    """

    def __init__(self, tools: Mapping[str, ReviewedTool], *, run_id: Callable[[Any], str]) -> None:
        if not isinstance(tools, Mapping) or not tools:
            raise OversightError("tools must map each tool name to a ReviewedTool")
        for name, tool in tools.items():
            if not isinstance(name, str) or not isinstance(tool, ReviewedTool):
                raise OversightError("tools must map each tool name to a ReviewedTool")
        if not callable(run_id):
            raise OversightError("run_id must be a function of the run's context")
        self._tools = dict(tools)
        self._run_id = run_id

    def function_tool(
        self,
        name: str,
        *,
        description: str,
        params_json_schema: dict[str, Any],
        strict_json_schema: bool = True,
    ) -> FunctionTool:
        """The tool to give your agent. It needs approval, and has no time limit or output
        guardrail.
        """
        tool = self._tool(name)

        async def invoke(context: Any, arguments: str) -> str:
            # The agent may have been rebuilt since the pause.
            self._check_settings(context.agent)
            key = _key(self._run_id(context.context), context.tool_call_id)
            parsed = _arguments(arguments)

            def work() -> str:
                try:
                    return reply_text(tool.run(key, parsed).reply)
                except asyncio.CancelledError as error:
                    # The SDK would turn this into a message for the model and carry on.
                    raise ToolStopped("the write was cancelled from inside") from error

            return await asyncio.to_thread(work)

        return FunctionTool(
            name=name,
            description=description,
            params_json_schema=params_json_schema,
            strict_json_schema=strict_json_schema,
            on_invoke_tool=invoke,
            needs_approval=True,
        )

    def submit(self, result: Any) -> None:
        """Send every call that ``result`` is paused on to review. Safe to repeat.

        Call it after every ``Runner.run`` that pauses. Calls to other tools are left to you.
        """
        ours = [item for item in result.interruptions if item.name in self._tools]
        if not ours:
            return
        self._check_settings(result.last_agent)
        run = self._run_id(result.context_wrapper.context)
        # All checked before any is submitted.
        calls = [(item, _key(run, item.call_id), _arguments(item.arguments)) for item in ours]
        for item, key, arguments in calls:
            self._tools[item.name].submit(key, arguments)

    def apply_decisions(self, state: Any, run_id: str) -> bool:
        """Approve or reject, on ``state``, each waiting call that has been decided.

        Returns True if there was any, so that ``Runner.run(agent, state)`` will get further.
        Pass the run's ID. Never use ``always_approve``: it lets later calls run with no
        pause. They would be refused, because nothing sent them to review.
        """
        applied = False
        for item in state.get_interruptions():
            if item.name not in self._tools:
                continue
            decided = self._tools[item.name].state(_key(run_id, item.call_id))
            if decided is CallState.WAITING:
                continue
            if decided is CallState.REJECT:
                state.reject(item, rejection_message=NOT_APPROVED)
            else:
                state.approve(item)
            applied = True
        return applied

    def _tool(self, name: str) -> ReviewedTool:
        if name not in self._tools:
            raise OversightError("there is no reviewed tool of that name")
        return self._tools[name]

    def _check_settings(self, agent: Any) -> None:
        """Refuse settings under which the model is told something else than what happened,
        and might try again. Checked at the pause, and again just before a write.
        """
        for tool in getattr(agent, "tools", ()):
            name = getattr(tool, "name", None)
            if name not in self._tools:
                continue
            if getattr(tool, "timeout_seconds", None) is not None:
                raise OversightError(
                    f"the reviewed tool {name!r} has a time limit. The model would be told it "
                    "failed while the write went on. Remove timeout_seconds."
                )
            if getattr(tool, "tool_output_guardrails", None):
                raise OversightError(
                    f"the reviewed tool {name!r} has an output guardrail. It could tell the "
                    "model a finished write was blocked. Remove tool_output_guardrails."
                )


def _key(run_id: Any, call_id: Any) -> str:
    for what, value in (("run ID", run_id), ("tool-call ID", call_id)):
        if not isinstance(value, str) or not value:
            raise OversightError(f"the {what} must be text")
    # As a JSON list, so that no two pairs can spell the same key.
    return json.dumps([run_id, call_id])


def _arguments(text: str) -> dict[str, Any]:
    try:
        arguments = json.loads(text)
    except (TypeError, ValueError, RecursionError) as error:
        raise OversightError("the model's tool arguments are not JSON") from error
    if not isinstance(arguments, dict):
        raise OversightError("the model's tool arguments must be an object")
    return arguments
