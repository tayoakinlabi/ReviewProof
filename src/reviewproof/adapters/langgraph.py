"""LangGraph (ADR-005).

LangGraph runs a paused step again from its start. A step with two reviewed calls would reach
the first one's write again when it resumed for the second. So ReviewProof brings its own step,
``ReviewStep``, which takes **one call**: give each call its own with ``sends()``. Do not call a
``ReviewedTool`` from a step you share with other calls, such as LangGraph's tool node.

Errors from the step always stop the run. **Do not give it a retry policy.** A retry cannot
write again, but it replaces the first error with a less useful one.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from reviewproof.adapters.tool import ReviewedTool
from reviewproof.errors import OversightError
from reviewproof.models import CallState

try:
    from langchain_core.messages import ToolMessage
    from langgraph.config import get_config
    from langgraph.types import Command, Send, interrupt
except ImportError as error:  # pragma: no cover - needs an install without LangGraph
    raise ImportError(
        'the LangGraph adapter needs LangGraph: pip install "reviewproof[langgraph]"'
    ) from error

_MARK = "reviewproof"
PAUSE = "A review is pending."


class ReviewStep:
    """A graph node that takes one call of a reviewed tool through review.

    ``tools`` maps each tool name the model uses to its ``ReviewedTool``. Add the step with
    ``builder.add_node(name, step)`` and send it calls from a routing function with
    ``sends()``. The graph needs a checkpointer, and each run a ``thread_id``.
    """

    def __init__(
        self, tools: Mapping[str, ReviewedTool], *, messages_key: str = "messages"
    ) -> None:
        if not isinstance(tools, Mapping) or not tools:
            raise OversightError("tools must map each tool name to a ReviewedTool")
        for name, tool in tools.items():
            if not isinstance(name, str) or not isinstance(tool, ReviewedTool):
                raise OversightError("tools must map each tool name to a ReviewedTool")
        self._tools = dict(tools)
        self._messages_key = messages_key

    def sends(self, node: str, calls: Iterable[Mapping[str, Any]]) -> list[Send]:
        """One ``Send`` to ``node`` for each of ``calls`` that is to a reviewed tool.

        Pass the model message's ``tool_calls``. Route any other calls yourself.
        """
        return [Send(node, dict(call)) for call in calls if call.get("name") in self._tools]

    def __call__(self, call: Any) -> dict[str, list[ToolMessage]]:
        name, arguments, call_id = self._one_call(call)
        tool = self._tools[name]
        key = _key(call_id)
        tool.submit(key, arguments)
        # The value says which action is waiting. It holds no ticket and no content. What
        # LangGraph returns on resume is ignored: ReviewProof's record decides.
        interrupt({_MARK: {"status": PAUSE, "tool": name, "key": key}})
        outcome = tool.run(key, arguments)
        message = ToolMessage(content=_text(outcome.reply), tool_call_id=call_id, name=name)
        return {self._messages_key: [message]}

    def resume_command(self, graph: Any, config: Mapping[str, Any]) -> Command | None:
        """What to resume ``graph`` with: every pause whose action has been decided.

        Returns None if there is none. Call it after each decision, for the run's ``config``,
        and pass the result to ``graph.invoke``. A pause that has been resumed is no longer
        pending, so one pause is not offered twice, unless two processes ask at once. Even
        then nothing is written twice.
        """
        ready = {}
        for pause in graph.get_state(config).interrupts:
            value = pause.value
            waiting = value.get(_MARK) if isinstance(value, Mapping) else None
            if not isinstance(waiting, Mapping) or waiting.get("tool") not in self._tools:
                continue
            if self._tools[waiting["tool"]].state(waiting["key"]) is not CallState.WAITING:
                ready[pause.id] = True
        return Command(resume=ready) if ready else None

    def _one_call(self, call: Any) -> tuple[str, Mapping[str, Any], str]:
        if not isinstance(call, Mapping) or not {"name", "args", "id"} <= set(call):
            raise OversightError(
                "this step takes one tool call. Send it each call with sends() from a routing "
                "function, not the graph state. Nothing was executed."
            )
        name, call_id = call["name"], call["id"]
        if name not in self._tools:
            raise OversightError("this step has no reviewed tool of that name")
        if not isinstance(call_id, str) or not call_id:
            raise OversightError("the tool call has no ID")
        return name, call["args"], call_id


def _key(call_id: str) -> str:
    """The thread ID and the model's tool-call ID, which a replay keeps."""
    try:
        config = get_config()
    except RuntimeError as error:
        # LangGraph cannot pause inside ainvoke before Python 3.11.
        raise OversightError(
            "LangGraph's run settings are not available here. Under Python 3.10, run the graph "
            "with invoke, not ainvoke. Nothing was executed."
        ) from error
    thread = config.get("configurable", {}).get("thread_id")
    if not isinstance(thread, str) or not thread:
        raise OversightError("the run needs a text thread_id in its configurable settings")
    # As a JSON list, so that no two pairs can spell the same key.
    return json.dumps([thread, call_id])


def _text(reply: Any) -> str:
    """A tool result as text. It must not fail: by now the write has happened."""
    if isinstance(reply, str):
        return reply
    try:
        return json.dumps(reply, default=str, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        return str(reply)
