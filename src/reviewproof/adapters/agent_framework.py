"""Microsoft Agent Framework (ADR-005).

A run pauses with an approval request for each call to a tool that needs approval. **The calls
run only once every request has been answered,** so a run resumes once everything it waits for
has been decided. **Always run the agent with a session:** in a test without one, the answer to
a pause was ignored, nothing was written and the model carried on.

**Any failure of a reviewed tool raises ``RunStopped``,** which is deliberately not an
``Exception``. The library turns a tool's errors into a message for the model. It has its own
signal for aborting a run, ``MiddlewareFailure``, but a host middleware that recovers from
errors catches that one too.
"""

from __future__ import annotations

import contextvars
import json
from collections.abc import Iterable, Mapping
from typing import Any

from reviewproof.adapters.tool import ReviewedTool, reply_text
from reviewproof.errors import OversightError
from reviewproof.models import CallState

try:
    from agent_framework import FunctionInvocationContext, FunctionTool, Message
except ImportError as error:  # pragma: no cover - needs an install without the framework
    raise ImportError(
        "the Agent Framework adapter needs agent-framework-core, and Python 3.11 or later: "
        'pip install "reviewproof[agent-framework]"'
    ) from error

#: Kept in the session's state, so that a failed session's reviewed tools write nothing more.
FAILED = "reviewproof_failed"
#: The parameter the library fills with its context. No tool argument may have this name.
_CONTEXT = "reviewproof_context"
_CALL: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "reviewproof_call", default=None
)


class RunStopped(BaseException):
    """A reviewed tool failed, and the agent's run must end. ``__cause__`` says why.

    **``except Exception`` does not catch this.** Catch it by name where you run the agent.
    After it, do not use the session again: check the target system, because the write may have
    happened, and so may others answered in the same run.
    """


class _ReviewedFunction(FunctionTool):
    """A function tool that learns the ID of each call, and lets no error after its function
    has started reach the model.
    """

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        call = {"id": kwargs.get("tool_call_id"), "started": False}
        token = _CALL.set(call)
        try:
            return await super().invoke(*args, **kwargs)
        except (KeyboardInterrupt, SystemExit, RunStopped):
            raise
        except BaseException as error:
            if not call["started"]:
                raise  # such as arguments that do not fit the schema: nothing has run
            raise RunStopped(f"a reviewed tool failed ({type(error).__name__})") from error
        finally:
            _CALL.reset(token)


class ReviewTools:
    """The reviewed tools of an agent.

    ``tools`` maps each tool name to its ``ReviewedTool``. The key of a call is the session's
    ID with the tool-call ID.

    Run the agent with a session. While a response has ``user_input_requests``, call
    ``submit()`` and keep the session and the requests. After decisions, ``decisions()`` gives
    the message to run the agent with, **with the same session**.
    """

    def __init__(self, tools: Mapping[str, ReviewedTool]) -> None:
        if not isinstance(tools, Mapping) or not tools:
            raise OversightError("tools must map each tool name to a ReviewedTool")
        for name, tool in tools.items():
            if not isinstance(name, str) or not isinstance(tool, ReviewedTool):
                raise OversightError("tools must map each tool name to a ReviewedTool")
        self._tools = dict(tools)

    def tool(self, name: str, *, description: str, input_schema: dict[str, Any]) -> FunctionTool:
        """The tool to give your agent. It always requires approval."""
        if name not in self._tools:
            raise OversightError("there is no reviewed tool of that name")
        if _CONTEXT in input_schema.get("properties", {}):
            raise OversightError(f"no argument of a reviewed tool may be called {_CONTEXT}")
        reviewed = self._tools[name]

        # The library finds the context by this annotation and passes it by name.
        def call(reviewproof_context: FunctionInvocationContext, **arguments: Any) -> str:
            return _run(reviewed, reviewproof_context.session, arguments)

        return _ReviewedFunction(
            name=name,
            description=description,
            func=call,
            input_model=input_schema,
            approval_mode="always_require",
        )

    def submit(self, requests: Iterable[Any], session: Any) -> None:
        """Send every call to a reviewed tool among ``requests`` to review. Safe to repeat.

        Pass ``response.user_input_requests`` after every run that has any, and the run's
        session. Other requests are left to you.
        """
        calls = []
        for request in self._ours(requests):
            function = request.function_call
            key = _key(session, function.call_id)
            calls.append((function.name, key, _arguments(function.arguments)))
        # All checked before any is submitted.
        for name, key, arguments in calls:
            self._tools[name].submit(key, arguments)

    def decisions(self, requests: Iterable[Any], session: Any) -> Message | None:
        """The message that answers the requests for reviewed tools, once **all** of them have
        been decided. Until then, and if there are none, None.

        If the run also waits on other requests, add your answers to the message's
        ``contents``: the library runs nothing until every request is answered. Run the agent
        with the message **and the same session**.
        """
        answers = []
        for request in self._ours(requests):
            function = request.function_call
            decided = self._tools[function.name].state(_key(session, function.call_id))
            if decided is CallState.WAITING:
                return None
            # A rejected call is refused to the library, so its tool never runs.
            answers.append(request.to_function_approval_response(decided is not CallState.REJECT))
        return Message(role="user", contents=answers) if answers else None

    def _ours(self, requests: Iterable[Any]) -> list[Any]:
        return [
            request
            for request in requests
            if request.type == "function_approval_request"
            and request.function_call.name in self._tools
        ]


def _run(reviewed: ReviewedTool, session: Any, arguments: dict[str, Any]) -> str:
    call = _CALL.get()
    if call is not None:
        call["started"] = True
    try:
        if session is None:
            raise OversightError("run the agent with a session. Nothing was executed.")
        if session.state.get(FAILED):
            raise OversightError(
                "an earlier reviewed call failed in this session. Nothing was executed."
            )
        key = _key(session, None if call is None else call["id"])
        return reply_text(reviewed.run(key, arguments).reply)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        if session is not None:
            session.state[FAILED] = True
        # Raised as it is, the library would tell the model the call failed, and carry on.
        raise RunStopped(f"a reviewed tool failed ({type(error).__name__})") from error


def _key(session: Any, call_id: Any) -> str:
    run = getattr(session, "session_id", None)
    for what, value in (("session ID", run), ("tool-call ID", call_id)):
        if not isinstance(value, str) or not value:
            raise OversightError(f"the {what} must be text. Nothing was executed.")
    # As a JSON list, so that no two pairs can spell the same key.
    return json.dumps([run, call_id])


def _arguments(arguments: Any) -> dict[str, Any]:
    """Parsed here: the library's own parser wraps what it cannot read instead of refusing."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, RecursionError) as error:
            raise OversightError("the model's tool arguments are not JSON") from error
    if not isinstance(arguments, Mapping):
        raise OversightError("the model's tool arguments must be an object")
    return dict(arguments)
