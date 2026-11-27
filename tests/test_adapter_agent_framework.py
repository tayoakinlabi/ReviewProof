"""The Microsoft Agent Framework adapter, with the real library and a scripted model.

Skipped unless it is installed: ``pip install -e ".[dev,agent-framework]"``. It needs Python
3.11 or later.
"""

import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest

pytest.importorskip("agent_framework")

from agent_framework import (
    Agent,
    AgentSession,
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationContext,
    FunctionInvocationLayer,
    FunctionTool,
    Message,
    MiddlewareFailure,
    ResponseStream,
    ToolApprovalMiddleware,
    create_always_approve_tool_response,
    function_middleware,
)
from conformance import PROPOSAL, SECOND, AdapterConformance, Harness, World
from deploy import ITEMS

from reviewproof import GuardError, OversightError, TransitionError
from reviewproof.adapters.agent_framework import FAILED, ReviewTools, RunStopped

ROOT = Path(__file__).parents[1]
TOOL = "write_record"
SCHEMA = {
    "type": "object",
    "properties": {"record_id": {"type": "string"}},
    "required": ["record_id"],
}


def calls(proposals, start=0, name=TOOL):
    return [(f"call-{n}", name, json.dumps(p)) for n, p in enumerate(proposals, start)]


class Script(FunctionInvocationLayer, BaseChatClient):
    """A model that first asks for ``turns[0]``, and so on. Then it answers in text."""

    def __init__(self, *turns):
        super().__init__()
        self.turns = list(turns)
        self.asked = 0
        self.given = []

    def _next(self, messages):
        self.asked += 1
        self.given = [
            str(content.result)
            for message in messages
            for content in message.contents
            if content.type == "function_result"
        ]
        if self.turns:
            return [
                Content.from_function_call(call_id, name, arguments=arguments)
                for call_id, name, arguments in self.turns.pop(0)
            ]
        return [Content.from_text("done")]

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        if stream:

            async def updates():
                yield ChatResponseUpdate(role="assistant", contents=self._next(messages))

            return ResponseStream(updates(), finalizer=ChatResponse.from_updates)

        async def reply():
            return ChatResponse(messages=[Message(role="assistant", contents=self._next(messages))])

        return reply()


def run(coroutine):
    """Hand the tests ReviewProof's own errors, so that they can name them."""
    try:
        return asyncio.run(coroutine)
    except RunStopped as stopped:
        if isinstance(stopped.__cause__, OversightError):
            raise stopped.__cause__ from None
        raise


class AgentFrameworkHarness(Harness):
    """The session and the requests are kept as text, as a host that restarts would."""

    key = json.dumps(["run-1", "call-0"])
    resumes_each_call_alone = False
    # The library words this itself.
    rejection_reply = "Error: Tool call invocation was rejected by user."

    def __init__(self, tool, *proposals):
        super().__init__(tool, *proposals)
        self.model = Script(calls(proposals))
        self.saved = self.at = self.review = self.agent = None

    def build(self, approval=None, **options):
        # Built late: a test may swap the tool before the agent starts.
        self.review = ReviewTools({TOOL: self.tool})
        function = self.review.tool(TOOL, description="Write.", input_schema=SCHEMA)
        if approval:  # otherwise the tool is left as the adapter built it
            function.approval_mode = approval
        self.agent = Agent(client=self.model, tools=[function], **options)
        return self.agent

    def _keep(self, response, session):
        requests = response.user_input_requests
        self.review.submit(requests, session)
        self.saved = (
            json.dumps(session.to_dict()),
            json.dumps([request.to_dict() for request in requests]),
        )

    def _load(self):
        session, requests = self.at or self.saved
        return (
            AgentSession.from_dict(json.loads(session)),
            [Content.from_dict(request) for request in json.loads(requests)],
        )

    def run_agent(self, message, session):
        return run(self.agent.run(message, session=session))

    def start(self):
        self.build()
        session = AgentSession(session_id="run-1")
        response = self.run_agent("go", session)
        assert response.user_input_requests
        self._keep(response, session)

    def saved_state(self):
        return "".join(self.saved)

    def resume(self):
        session, requests = self._load()
        message = self.review.decisions(requests, session)
        if message is not None:
            self._keep(self.run_agent(message, session), session)

    def force_resume(self):
        session, requests = self._load()
        approved = [request.to_function_approval_response(True) for request in requests]
        self.run_agent(Message(role="user", contents=approved), session)

    def run_without_pause(self):
        self.build(approval="never_require")
        self.run_agent("go", AgentSession(session_id="run-1"))

    def snapshot(self):
        return self.saved

    def restore(self, snapshot):
        self.at = snapshot

    def replies(self):
        return list(self.model.given)

    def model_calls(self):
        return self.model.asked


class TestAgentFrameworkAdapter(AdapterConformance):
    harness = AgentFrameworkHarness


# ----------------------------------------------------------------- this library alone


def started(make, *proposals):
    world = World(make, AgentFrameworkHarness, proposals or (PROPOSAL,))
    world.agent.start()
    return world


def approve(world, proposal=PROPOSAL):
    world.decide(world.real_token(proposal))


def resume_with(world, **agent_options):
    """Rebuild the agent, as a second process would, then resume."""
    world.agent.build(**agent_options)
    world.agent.resume()


@function_middleware
async def passes_errors_on(context, call_next):
    await call_next()


RECOVERED = []


@function_middleware
async def recovers_from_errors(context, call_next):
    try:
        await call_next()
    except Exception as error:
        RECOVERED.append(error)
        context.result = "all is well"


MIDDLEWARE = {
    "none": None,
    "passing-errors-on": [passes_errors_on],
    "recovering": [recovers_from_errors],
}


class _Outside(BaseException):
    pass


@pytest.mark.parametrize("middleware", MIDDLEWARE)
@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("down"),
        MiddlewareFailure("the library's own signal"),
        _Outside("x"),
        asyncio.CancelledError(),
    ],
    ids=["ordinary", "middleware-failure", "outside-exception", "cancelled"],
)
def test_no_failure_reaches_the_model(make, offline, middleware, error):
    RECOVERED.clear()
    world = started(make)
    approve(world)
    world.failure = error
    with pytest.raises(RunStopped) as stopped:
        resume_with(world, middleware=MIDDLEWARE[middleware])
    assert stopped.value.__cause__ is error
    assert RECOVERED == []
    assert world.agent.model_calls() == 1
    assert len(world.written) == 1


def test_run_stopped_is_not_an_exception():
    """So that a middleware that recovers from errors cannot turn it into a result."""
    assert not issubclass(RunStopped, Exception)


@pytest.mark.parametrize("middleware", ["none", "passing-errors-on"])
def test_it_works_with_and_without_host_middleware(make, offline, middleware):
    """Without any middleware the library gives a tool less to go on."""
    world = started(make)
    approve(world)
    resume_with(world, middleware=MIDDLEWARE[middleware])
    assert world.written == [PROPOSAL]


def test_a_streamed_run_stops_too(make, offline):
    world = World(make, AgentFrameworkHarness)
    agent = world.agent.build()
    session = AgentSession(session_id="run-1")

    async def stream(message):
        responses = agent.run(message, stream=True, session=session)
        async for _ in responses:
            pass
        return await responses.get_final_response()

    first = run(stream("go"))
    world.agent.review.submit(first.user_input_requests, session)
    approve(world)
    world.failure = ConnectionError("down")
    message = world.agent.review.decisions(first.user_input_requests, session)
    with pytest.raises(RunStopped):
        run(stream(message))
    assert world.agent.model_calls() == 1
    assert len(world.written) == 1


def test_nothing_runs_until_every_call_is_answered(make, offline):
    world = started(make, PROPOSAL, SECOND)
    session, requests = world.agent._load()
    review = world.agent.review
    approve(world)
    assert review.decisions(requests, session) is None
    world.decide(world.real_token(SECOND), "reject")
    message = review.decisions(requests, session)
    assert [content.approved for content in message.contents] == [True, False]


def test_a_rejected_call_never_runs_the_tool(make, offline, monkeypatch):
    world = started(make)
    invoked = []
    real_run = world.tool.run
    monkeypatch.setattr(world.tool, "run", lambda *a: invoked.append(a) or real_run(*a))
    world.decide(world.real_token(PROPOSAL), "reject")
    world.agent.resume()
    assert invoked == [] and world.written == []
    assert world.agent.replies() == [AgentFrameworkHarness.rejection_reply]


def test_a_failed_session_writes_nothing_more(make, offline):
    """The library would still ask the model, but no reviewed tool of the session runs."""
    world = World(make, AgentFrameworkHarness)
    world.agent.model = Script(calls([PROPOSAL]), calls([SECOND], start=1))
    agent = world.agent.build()
    session = AgentSession(session_id="run-1")
    first = run(agent.run("go", session=session))
    world.agent.review.submit(first.user_input_requests, session)
    approve(world)
    world.failure = ConnectionError("down")
    message = world.agent.review.decisions(first.user_input_requests, session)
    with pytest.raises(RunStopped):
        run(agent.run(message, session=session))
    assert session.state[FAILED] is True
    assert AgentSession.from_dict(session.to_dict()).state[FAILED] is True

    world.failure = None
    again = run(agent.run("carry on", session=session))
    world.agent.review.submit(again.user_input_requests, session)
    approve(world, SECOND)
    message = world.agent.review.decisions(again.user_input_requests, session)
    with pytest.raises(OversightError, match="failed in this session"):
        run(agent.run(message, session=session))
    assert [a["record_id"] for a in world.written] == [PROPOSAL["record_id"]]


def test_a_standing_approval_cannot_write_what_was_not_reviewed(make, offline):
    world = World(make, AgentFrameworkHarness)
    world.agent.model = Script(calls([PROPOSAL]), calls([SECOND], start=1))
    agent = world.agent.build(middleware=[ToolApprovalMiddleware()])
    session = AgentSession(session_id="run-1")
    first = run(agent.run("go", session=session))
    world.agent.review.submit(first.user_input_requests, session)
    approve(world)
    standing = create_always_approve_tool_response(first.user_input_requests[0])
    with pytest.raises(OversightError, match="nothing in this queue"):
        run(agent.run(Message(role="user", contents=[standing]), session=session))
    assert world.written == [PROPOSAL]


def test_restoring_a_session_twice_writes_once(make, offline):
    world = started(make)
    saved = world.agent.snapshot()
    approve(world)
    world.agent.resume()
    world.agent.restore(saved)
    with pytest.raises(TransitionError, match="already been requested"):
        world.agent.resume()
    assert world.written == [PROPOSAL]


def test_without_a_session_nothing_is_written(make, offline):
    """The library ignores the answer. The adapter cannot raise: its tool is never called."""
    world = World(make, AgentFrameworkHarness)
    agent = world.agent.build()
    first = run(agent.run("go"))
    approved = first.user_input_requests[0].to_function_approval_response(True)
    run(agent.run(["go", *first.messages, Message(role="user", contents=[approved])]))
    assert world.written == []


@pytest.mark.parametrize("session", [None, object()])
def test_submit_needs_the_session(make, offline, session):
    world = started(make)
    _, requests = world.agent._load()
    with pytest.raises(OversightError, match="session ID"):
        world.agent.review.submit(requests, session)


def test_the_tool_without_a_session_or_a_call_id_writes_nothing(make, offline):
    world = World(make, AgentFrameworkHarness)
    function = ReviewTools({TOOL: world.tool}).tool(TOOL, description="w", input_schema=SCHEMA)
    with pytest.raises(RunStopped) as stopped:
        asyncio.run(function.invoke(arguments=dict(PROPOSAL), tool_call_id="call-0"))
    assert str(stopped.value.__cause__) == "run the agent with a session. Nothing was executed."
    assert world.written == []


def invoke_as_the_library_does(function, session):
    context = FunctionInvocationContext(
        function=function, arguments=dict(PROPOSAL), session=session
    )
    return asyncio.run(function.invoke(context=context, tool_call_id="call-0"))


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_a_request_to_stop_the_program_is_passed_on(make, offline, error):
    world = World(make, AgentFrameworkHarness)
    function = ReviewTools({TOOL: world.tool}).tool(TOOL, description="w", input_schema=SCHEMA)
    session = AgentSession(session_id="s")

    def stop(*arguments):
        raise error

    world.tool.run = stop
    with pytest.raises(error):
        invoke_as_the_library_does(function, session)


def test_the_function_itself_raises_run_stopped(make, offline):
    """The inner of the two layers: it holds even if the library stopped calling invoke."""
    world = World(make, AgentFrameworkHarness)
    function = ReviewTools({TOOL: world.tool}).tool(TOOL, description="w", input_schema=SCHEMA)
    session = AgentSession(session_id="s")
    context = FunctionInvocationContext(function=function, arguments={}, session=session)
    with pytest.raises(RunStopped):
        function.func(context, record_id="x")  # no call ID outside invoke, so it fails
    assert session.state[FAILED] is True


def test_an_error_after_the_function_started_also_stops_the_run(make, offline, monkeypatch):
    """Such as one while the library handles a result, after the write."""
    world = World(make, AgentFrameworkHarness)
    function = ReviewTools({TOOL: world.tool}).tool(TOOL, description="w", input_schema=SCHEMA)
    real_invoke = FunctionTool.invoke

    async def then_fail(self, *args, **kwargs):
        await real_invoke(self, *args, **kwargs)
        raise ValueError("while handling the result")

    world.tool.run = lambda *arguments: type("Outcome", (), {"reply": "ok"})()
    monkeypatch.setattr(FunctionTool, "invoke", then_fail)
    with pytest.raises(RunStopped) as stopped:
        invoke_as_the_library_does(function, AgentSession(session_id="s"))
    assert isinstance(stopped.value.__cause__, ValueError)


def test_arguments_that_do_not_fit_run_nothing(make, offline):
    """The library may report this to the model: the function never started."""
    world = World(make, AgentFrameworkHarness)
    function = ReviewTools({TOOL: world.tool}).tool(TOOL, description="w", input_schema=SCHEMA)
    with pytest.raises(Exception) as refused:
        asyncio.run(function.invoke(arguments={}, tool_call_id="call-0"))
    assert not isinstance(refused.value, RunStopped)
    assert world.written == []


def test_an_edit_that_was_not_recorded_writes_nothing(make, offline):
    world = started(make)
    real = world.real_token(PROPOSAL)
    recorded = {**world.queue[real], "classification_rationale": "recorded"}
    world.decide(real, "edit", recorded)
    world.edits[world.agent.key] = {**recorded, "classification_rationale": "something else"}
    with pytest.raises(GuardError):
        world.agent.resume()
    assert world.written == []


def test_other_requests_are_left_to_the_host(make, offline):
    world = World(make, AgentFrameworkHarness)
    review = ReviewTools({TOOL: world.tool})
    other = Content.from_function_approval_request(
        "r", Content.from_function_call("c", "other", arguments="{}")
    )
    session = AgentSession()
    review.submit([other], session)
    assert review.decisions([other], session) is None
    assert world.queue == {}


@pytest.mark.parametrize(
    ("arguments", "message"), [("not json", "not JSON"), ("[1]", "must be an object")]
)
def test_arguments_that_cannot_be_reviewed(make, offline, arguments, message):
    world = World(make, AgentFrameworkHarness)
    review = ReviewTools({TOOL: world.tool})
    requests = [
        Content.from_function_approval_request(
            f"r{n}", Content.from_function_call(f"call-{n}", TOOL, arguments=text)
        )
        for n, text in enumerate([json.dumps(PROPOSAL), arguments])
    ]
    with pytest.raises(OversightError, match=message):
        review.submit(requests, AgentSession())
    assert world.queue == {}, "no call is submitted if any cannot be"


@pytest.mark.parametrize("tools", [{}, None, {TOOL: print}, {1: None}])
def test_bad_tools(tools):
    with pytest.raises(OversightError, match="ReviewedTool"):
        ReviewTools(tools)


def test_bad_settings(make, offline):
    world = World(make, AgentFrameworkHarness)
    review = ReviewTools({TOOL: world.tool})
    with pytest.raises(OversightError, match="no reviewed tool"):
        review.tool("other", description="o", input_schema=SCHEMA)
    taken = {"type": "object", "properties": {"reviewproof_context": {"type": "string"}}}
    with pytest.raises(OversightError, match="may be called"):
        review.tool(TOOL, description="o", input_schema=taken)


def test_the_tool_requires_approval(make, offline):
    world = World(make, AgentFrameworkHarness)
    function = world.agent.build().default_options["tools"][0]
    assert function.approval_mode == "always_require"


# ----------------------------------------------------------------- the guide


def test_the_python_in_the_agent_framework_guide_runs(tmp_path, monkeypatch, offline):
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)
    text = (ROOT / "docs" / "guides" / "agent-framework.md").read_text(encoding="utf-8")
    [snippet] = re.findall(r"^```python\n(.*?)^```$", text, flags=re.MULTILINE | re.DOTALL)
    deviation = {**ITEMS[0], "record_id": "QMS-801"}
    namespace = {"model_client": Script(calls([deviation], name="save_deviation"))}
    exec(compile(snippet, "agent-framework.md", "exec"), namespace)  # noqa: S102
    assert namespace["written"] == ["QMS-801"]
