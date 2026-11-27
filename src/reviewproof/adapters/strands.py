"""Strands Agents (ADR-005).

``ReviewTools`` is both the builder of the reviewed tools and a hook provider. **Pass it in
``Agent(hooks=[...])``:** without it the tools refuse to write.

A hook pauses the run before each reviewed call, each on its own, so each can be resumed on its
own. When the tool runs after that, it runs again from its start, which is safe.

**Stopping after a failure takes two checks.** By default Strands runs tools together and turns a
tool's error into a message for the model, and an error outside ``Exception`` is lost without a
trace. So a reviewed tool raises nothing: it marks the agent as failed, and the hooks raise
``RunStopped`` before the model is asked again, and also when the invocation ends without asking
it, as when another call is still waiting for review. A failed agent stays failed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from reviewproof.adapters.tool import ReviewedTool, reply_text
from reviewproof.errors import OversightError
from reviewproof.models import CallState

try:
    from strands.hooks import (
        AfterInvocationEvent,
        BeforeModelCallEvent,
        BeforeToolCallEvent,
        HookProvider,
    )
    from strands.tools import PythonAgentTool
except ImportError as error:  # pragma: no cover - needs an install without Strands
    raise ImportError(
        'the Strands adapter needs Strands Agents: pip install "reviewproof[strands]"'
    ) from error

PAUSE = "A review is pending."
#: The name of the pauses this adapter raises, and the mark in their reason.
INTERRUPT = "reviewproof"
#: All that the model's history ever says about a failure.
STOPPING = "This action did not complete. The run is stopping."
#: Kept in the agent's state, so that a saved session remembers it.
FAILED = "reviewproof_failed"
_ERROR = "_reviewproof_error"
_PAUSED = "_reviewproof_paused"


class RunStopped(RuntimeError):
    """A reviewed tool failed, and the agent must not carry on. ``__cause__`` says why, in the
    process where it happened.

    Do not use this agent or its session again. Check the target system: the write may have
    happened, and so may others that were running at the same time.
    """


class ReviewTools(HookProvider):
    """The reviewed tools of an agent, and the hooks that guard them.

    ``tools`` maps each tool name to its ``ReviewedTool``. ``run_id(agent)`` returns the ID of
    the run, for example from ``agent.state``. It must be the same after a saved session is
    restored.

    Use it like this: ``Agent(tools=[review.tool(...)], hooks=[review])``. When a result's
    ``stop_reason`` is ``"interrupt"``, keep its ``interrupts``: the calls are already in your
    review queue. After decisions, pass ``review.resume_input(interrupts)`` to the agent.
    """

    def __init__(self, tools: Mapping[str, ReviewedTool], *, run_id: Callable[[Any], str]) -> None:
        if not isinstance(tools, Mapping) or not tools:
            raise OversightError("tools must map each tool name to a ReviewedTool")
        for name, tool in tools.items():
            if not isinstance(name, str) or not isinstance(tool, ReviewedTool):
                raise OversightError("tools must map each tool name to a ReviewedTool")
        if not callable(run_id):
            raise OversightError("run_id must be a function of the agent")
        self._tools = dict(tools)
        self._run_id = run_id
        self._built: list[PythonAgentTool] = []

    def tool(self, name: str, *, description: str, input_schema: dict[str, Any]) -> PythonAgentTool:
        """The tool to give your agent, with ``hooks=[review]``."""
        reviewed = self._tool(name)

        def call(tool_use: Mapping[str, Any], **invocation_state: Any) -> dict[str, Any]:
            return self._run(reviewed, tool_use, invocation_state.get("agent"))

        spec = {"name": name, "description": description, "inputSchema": {"json": input_schema}}
        built = PythonAgentTool(name, spec, call)
        self._built.append(built)
        return built

    def register_hooks(self, registry: Any, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(BeforeModelCallEvent, self._stop_if_failed)
        registry.add_callback(AfterInvocationEvent, self._stop_if_failed)

    def resume_input(self, interrupts: Iterable[Any]) -> list[dict[str, Any]] | None:
        """What to pass to the agent to resume: every pause whose action has been decided.

        Takes the ``interrupts`` of the latest result. Returns None if nothing is ready.
        Pauses that are not ReviewProof's are left to you.
        """
        ready = []
        for pause in interrupts:
            waiting = pause.reason.get(INTERRUPT) if isinstance(pause.reason, Mapping) else None
            if pause.name != INTERRUPT or not isinstance(waiting, Mapping):
                continue
            if waiting.get("tool") not in self._tools:
                continue
            if self._tools[waiting["tool"]].state(waiting["key"]) is not CallState.WAITING:
                # What the answer says is ignored: ReviewProof's record decides.
                response = {"interruptId": pause.id, "response": "decided"}
                ready.append({"interruptResponse": response})
        return ready or None

    # ------------------------------------------------------------- hooks

    def _before_tool(self, event: Any) -> None:
        name = event.tool_use.get("name")
        if name not in self._tools:
            return
        agent, call_id = event.agent, event.tool_use.get("toolUseId")
        try:
            if not any(event.selected_tool is built for built in self._built):
                raise OversightError(
                    f"the tool {name!r} was not built by review.tool(), so nothing would guard "
                    "what it writes"
                )
            self._require_can_run(agent)
            key = _key(self._run_id(agent), call_id)
            self._tools[name].submit(key, event.tool_use.get("input"))
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as error:
            _fail(agent, error)
            event.cancel_tool = STOPPING
            return
        # Outside the try: until it is answered, this raises Strands' own pause.
        event.interrupt(INTERRUPT, reason={INTERRUPT: {"status": PAUSE, "tool": name, "key": key}})
        _paused(agent).add(call_id)

    def _stop_if_failed(self, event: Any) -> None:
        if _has_failed(event.agent):
            raise RunStopped(
                "a reviewed tool failed. Do not use this agent again; check the target system."
            ) from getattr(event.agent, _ERROR, None)

    # ------------------------------------------------------------- the tool

    def _run(
        self, reviewed: ReviewedTool, tool_use: Mapping[str, Any], agent: Any
    ) -> dict[str, Any]:
        call_id = tool_use.get("toolUseId")
        try:
            if agent is None or call_id not in _paused(agent):
                raise OversightError(
                    "this call did not pass ReviewProof's pause. Give the agent "
                    "hooks=[review]. Nothing was executed."
                )
            _paused(agent).discard(call_id)
            self._require_can_run(agent)
            outcome = reviewed.run(_key(self._run_id(agent), call_id), tool_use.get("input"))
            status = "success" if outcome.executed else "error"
            return _result(call_id, status, reply_text(outcome.reply))
        except (KeyboardInterrupt, SystemExit) as error:
            _fail(agent, error)
            raise
        except BaseException as error:
            # Never raised: Strands would pass it to the model, or lose it.
            _fail(agent, error)
            return _result(call_id, "error", STOPPING)

    def _require_can_run(self, agent: Any) -> None:
        if _has_failed(agent):
            raise OversightError("an earlier reviewed call failed. Nothing was executed.")
        if getattr(agent, "_background_tasks", None) is not None:
            raise OversightError(
                "background tasks are turned on. The model would be answered before a write "
                "finished. Nothing was executed."
            )

    def _tool(self, name: str) -> ReviewedTool:
        if name not in self._tools:
            raise OversightError("there is no reviewed tool of that name")
        return self._tools[name]


def _paused(agent: Any) -> set[str]:
    """The calls that have been through the pause and not yet run, in this process."""
    calls = getattr(agent, _PAUSED, None)
    if calls is None:
        calls = set()
        setattr(agent, _PAUSED, calls)
    return calls


def _has_failed(agent: Any) -> bool:
    return getattr(agent, _ERROR, None) is not None or bool(agent.state.get(FAILED))


def _fail(agent: Any, error: BaseException) -> None:
    """Mark the agent. The first failure is the one kept."""
    if agent is None or getattr(agent, _ERROR, None) is not None:
        return
    setattr(agent, _ERROR, error)
    agent.state.set(FAILED, True)


def _result(call_id: Any, status: str, text: str) -> dict[str, Any]:
    return {"toolUseId": call_id, "status": status, "content": [{"text": text}]}


def _key(run_id: Any, call_id: Any) -> str:
    for what, value in (("run ID", run_id), ("tool-call ID", call_id)):
        if not isinstance(value, str) or not value:
            raise OversightError(f"the {what} must be text")
    # As a JSON list, so that no two pairs can spell the same key.
    return json.dumps([run_id, call_id])
