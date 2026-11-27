"""The Pydantic AI adapter, with the real library and a scripted model.

Skipped unless Pydantic AI is installed: ``pip install -e ".[dev,pydantic-ai]"``. It needs
Python 3.11 or later.
"""

import asyncio
import dataclasses
import json
import math
import re
import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("pydantic_ai")

from conformance import PROPOSAL, SECOND, AdapterConformance, Harness, World
from deploy import ITEMS
from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ModelRetry, ToolDenied
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ToolFailed
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel

from reviewproof import GuardError, OversightError, TransitionError
from reviewproof.adapters.pydantic_ai import ReviewTools, RunStopped
from reviewproof.adapters.tool import NOT_APPROVED

ROOT = Path(__file__).parents[1]
TOOL = "write_record"
SCHEMA = {"type": "object", "properties": {"record_id": {"type": "string"}}}
PAUSE = TypeAdapter(DeferredToolRequests)


@pytest.fixture(autouse=True)
def no_banner(monkeypatch):
    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "1")


def calls(proposals, start=0, name=TOOL):
    return [
        ToolCallPart(name, json.dumps(p), tool_call_id=f"call-{n}")
        for n, p in enumerate(proposals, start)
    ]


class Script:
    """A model that first asks for ``turns[0]``, and so on. Then it answers in text."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.asked = 0
        self.given = []

    def __call__(self, messages, info):
        self.asked += 1
        # Everything the model has been told about a tool call, retries included.
        self.given = [
            str(part.content)
            for message in messages
            for part in getattr(message, "parts", [])
            if isinstance(part, ToolReturnPart) or type(part).__name__ == "RetryPromptPart"
        ]
        if self.turns:
            return ModelResponse(parts=self.turns.pop(0))
        return ModelResponse(parts=[TextPart("done")])


def run_id(deps):
    return deps["run_id"]


DEPS = {"run_id": "run-1"}


def run(coroutine):
    """Hand the tests ReviewProof's own errors, so that they can name them."""
    try:
        return asyncio.run(coroutine)
    except RunStopped as stopped:
        if isinstance(stopped.__cause__, OversightError):
            raise stopped.__cause__ from stopped
        raise


class PydanticHarness(Harness):
    key = json.dumps(["run-1", "call-0"])
    resumes_each_call_alone = False

    def __init__(self, tool, *proposals):
        super().__init__(tool, *proposals)
        self.model = Script(calls(proposals))
        self.saved = self.at = self.review = self.agent = None

    def build(self, approval=True, changes=None, **agent_options):
        # Built late: a test may swap the tool before the agent starts.
        self.review = ReviewTools({TOOL: self.tool}, run_id=run_id)
        function = self.review.tool(TOOL, description="Write.", parameters_json_schema=SCHEMA)
        if not approval:  # otherwise the tool is left as the adapter built it
            function.requires_approval = False
        for name, value in (changes or {}).items():
            setattr(function, name, value)
        self.agent = Agent(
            FunctionModel(self.model),
            tools=[function],
            output_type=[str, DeferredToolRequests],
            **agent_options,
        )
        return self.agent

    def _keep(self, result):
        """What a host stores: the messages and the pause, as text."""
        if not isinstance(result.output, DeferredToolRequests):
            self.saved = (result.all_messages_json(), None)
            return
        self.review.submit(result.output, "run-1")
        self.saved = (result.all_messages_json(), PAUSE.dump_json(result.output))

    def _load(self):
        messages, pause = self.at or self.saved
        return ModelMessagesTypeAdapter.validate_json(messages), (
            None if pause is None else PAUSE.validate_json(pause)
        )

    def _continue(self, history, answers):
        return run(
            self.agent.run(message_history=history, deferred_tool_results=answers, deps=DEPS)
        )

    def start(self):
        self.build()
        result = run(self.agent.run("go", deps=DEPS))
        assert isinstance(result.output, DeferredToolRequests)
        self._keep(result)

    def saved_state(self):
        return "".join(part.decode("utf-8") for part in self.saved)

    def resume(self):
        history, pause = self._load()
        assert pause is not None
        answers = self.review.decisions(pause, "run-1")
        if answers is not None:
            self._keep(self._continue(history, answers))

    def force_resume(self):
        history, pause = self._load()
        approvals = {part.tool_call_id: True for part in pause.approvals}
        self._continue(history, DeferredToolResults(approvals=approvals))

    def run_without_pause(self):
        self.build(approval=False)
        run(self.agent.run("go", deps=DEPS))

    def snapshot(self):
        return self.saved

    def restore(self, snapshot):
        self.at = snapshot

    def replies(self):
        return list(self.model.given)

    def model_calls(self):
        return self.model.asked


class TestPydanticAIAdapter(AdapterConformance):
    harness = PydanticHarness


# ----------------------------------------------------------------- this library alone


def started(make, *proposals, **build):
    world = World(make, PydanticHarness, proposals or (PROPOSAL,))
    world.agent.start()
    return world


def approve_first(world):
    world.decide(world.real_token(PROPOSAL))


class _Reply(BaseModel):
    status: int


def _invalid_reply():
    _Reply.model_validate({"status": "not a number"})


def _raiser(error):
    def go():
        raise error

    return go


#: Each of these, raised as it is, would reach the model or change what the run does.
LIBRARY_ERRORS = {
    "ValidationError": (_invalid_reply, ValidationError),
    "ModelRetry": (_raiser(ModelRetry("try again")), ModelRetry),
    "ToolFailed": (_raiser(ToolFailed("it failed")), ToolFailed),
    "ApprovalRequired": (_raiser(ApprovalRequired()), ApprovalRequired),
    "CallDeferred": (_raiser(CallDeferred()), CallDeferred),
    "CancelledError": (_raiser(asyncio.CancelledError()), asyncio.CancelledError),
    "TimeoutError": (_raiser(TimeoutError("target")), TimeoutError),
}


@pytest.mark.parametrize("name", LIBRARY_ERRORS)
def test_no_error_from_a_write_reaches_the_model(make, offline, name):
    fail, kind = LIBRARY_ERRORS[name]
    world = started(make)
    approve_first(world)

    def write(**arguments):
        world.written.append(arguments)
        fail()  # after the write

    world.tool._write = write
    with pytest.raises(RunStopped) as stopped:
        world.agent.resume()
    assert isinstance(stopped.value.__cause__, kind)
    assert world.agent.model_calls() == 1
    assert len(world.written) == 1


def test_run_stopped_is_not_an_exception():
    """So that a hook that recovers from errors cannot turn it into a result."""
    assert not issubclass(RunStopped, Exception)


def test_a_hook_that_recovers_from_errors_cannot_hide_a_failed_write(make, offline):
    hooks = Hooks()
    recovered = []

    @hooks.on.tool_execute_error
    async def recover(ctx, *, call, tool_def, args, error):
        recovered.append(error)
        return "all is well"

    world = World(make, PydanticHarness)
    world.agent.build(capabilities=[hooks])
    result = run(world.agent.agent.run("go", deps=DEPS))
    world.agent._keep(result)
    approve_first(world)
    world.failure = ConnectionError("down")
    with pytest.raises(RunStopped):
        world.agent.resume()
    assert recovered == []
    assert world.agent.model_calls() == 1


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_a_request_to_stop_the_program_is_passed_on_unchanged(make, offline, error):
    world = World(make, PydanticHarness)
    function = ReviewTools({TOOL: world.tool}, run_id=run_id).tool(
        TOOL, description="w", parameters_json_schema=SCHEMA
    )

    class Context:
        tool_name = TOOL

        @property
        def tool_manager(self):
            raise error

    with pytest.raises(error):
        function.function(Context())


def test_the_tool_needs_approval_and_has_no_time_limit(make, offline):
    world = World(make, PydanticHarness)
    function = world.agent.build().toolsets[0].tools[TOOL]
    assert function.requires_approval is True
    assert function.timeout == math.inf


def test_an_agent_wide_time_limit_does_not_apply(make, offline):
    world = World(make, PydanticHarness)
    world.agent.build(tool_timeout=0.05)
    world.agent._keep(run(world.agent.agent.run("go", deps=DEPS)))
    approve_first(world)

    def slow_write(**arguments):
        time.sleep(0.3)
        world.written.append(arguments)
        return "record saved"

    world.tool._write = slow_write
    world.agent.resume()
    assert world.agent.replies() == ["record saved"]


@pytest.mark.parametrize("when", ["on the tool", "by a prepare function"])
def test_a_time_limit_added_to_the_tool_writes_nothing(make, offline, when):
    async def shorten(ctx, definition):
        return dataclasses.replace(definition, timeout=30.0)

    changes = {"timeout": 30.0} if when == "on the tool" else {"prepare": shorten}
    world = started(make)
    approve_first(world)
    world.agent.build(changes=changes)
    with pytest.raises(OversightError, match="time limit"):
        world.agent.resume()
    assert world.written == []
    assert world.agent.model_calls() == 1


def test_nothing_is_answered_until_every_call_is_decided(make, offline):
    world = started(make, PROPOSAL, SECOND)
    _, pause = world.agent._load()
    review = world.agent.review
    assert review.decisions(pause, "run-1") is None
    approve_first(world)
    assert review.decisions(pause, "run-1") is None
    world.decide(world.real_token(SECOND), "reject")
    answers = review.decisions(pause, "run-1")
    assert answers.approvals["call-0"] is True
    rejected = answers.approvals["call-1"]
    assert isinstance(rejected, ToolDenied) and rejected.message == NOT_APPROVED


def test_a_rejected_call_never_runs_the_tool(make, offline, monkeypatch):
    world = started(make)
    invoked = []
    real_run = world.tool.run
    monkeypatch.setattr(world.tool, "run", lambda *a: invoked.append(a) or real_run(*a))
    world.decide(world.real_token(PROPOSAL), "reject")
    world.agent.resume()
    assert invoked == [] and world.written == []
    assert world.agent.replies() == [NOT_APPROVED]


def test_a_later_call_goes_to_review_in_turn(make, offline):
    world = World(make, PydanticHarness)
    world.agent.model = Script(calls([PROPOSAL]), calls([SECOND], start=1))
    world.agent.start()
    approve_first(world)
    world.agent.resume()
    assert world.written == [PROPOSAL]
    world.decide(world.real_token(SECOND))
    world.agent.resume()
    assert world.written == [PROPOSAL, SECOND]


def test_resuming_an_older_pause_writes_nothing_twice(make, offline):
    world = started(make)
    older = world.agent.snapshot()
    approve_first(world)
    world.agent.resume()
    world.agent.restore(older)
    with pytest.raises(TransitionError, match="already been requested"):
        world.agent.resume()
    assert world.written == [PROPOSAL]


def test_an_edit_that_was_not_recorded_writes_nothing(make, offline):
    world = started(make)
    real = world.real_token(PROPOSAL)
    recorded = {**world.queue[real], "classification_rationale": "recorded"}
    world.decide(real, "edit", recorded)
    world.edits[world.agent.key] = {**recorded, "classification_rationale": "something else"}
    with pytest.raises(GuardError):
        world.agent.resume()
    assert world.written == []


def test_submitting_again_creates_nothing_new(make, offline):
    world = started(make)
    _, pause = world.agent._load()
    tickets, events = set(world.queue), world.events()
    world.agent.review.submit(pause, "run-1")
    assert (set(world.queue), world.events()) == (tickets, events)


def test_calls_to_other_tools_are_left_to_the_host(make, offline):
    world = World(make, PydanticHarness)
    review = ReviewTools({TOOL: world.tool}, run_id=run_id)
    pause = DeferredToolRequests(approvals=calls([{}], name="other"))
    review.submit(pause, "run-1")
    assert review.decisions(pause, "run-1") is None
    assert world.queue == {}


@pytest.mark.parametrize("arguments", ["not json", "[1]"])
def test_arguments_that_cannot_be_reviewed(make, offline, arguments):
    world = World(make, PydanticHarness)
    review = ReviewTools({TOOL: world.tool}, run_id=run_id)
    bad = ToolCallPart(TOOL, arguments, tool_call_id="call-1")
    pause = DeferredToolRequests(approvals=[*calls([PROPOSAL]), bad])
    with pytest.raises(OversightError, match="not a JSON object"):
        review.submit(pause, "run-1")
    assert world.queue == {}, "no call is submitted if any cannot be"


@pytest.mark.parametrize("value", ["", 7])
def test_a_run_needs_an_id(make, offline, value):
    world = World(make, PydanticHarness)
    review = ReviewTools({TOOL: world.tool}, run_id=run_id)
    with pytest.raises(OversightError, match="run ID"):
        review.submit(DeferredToolRequests(approvals=calls([PROPOSAL])), value)
    assert world.queue == {}


@pytest.mark.parametrize("name", ["context", "self", "arguments"])
def test_a_tool_argument_may_have_any_name(make, offline, name):
    world = started(make)
    approve_first(world)
    seen = []
    world.tool.run = lambda key, arguments: seen.append(arguments) or _Outcome()
    function = world.agent.review.tool(TOOL, description="w", parameters_json_schema=SCHEMA)
    context = _Context()
    assert function.function(context, **{name: 1}) == "ok"
    assert seen == [{name: 1}]


class _Outcome:
    reply = "ok"


class _Context:
    tool_name = TOOL
    tool_call_id = "call-0"
    deps = DEPS

    class tool_manager:  # noqa: N801 - stands in for the library's object
        class _Tool:
            class tool_def:  # noqa: N801
                timeout = math.inf

        tools = {TOOL: _Tool}


@pytest.mark.parametrize("tools", [{}, None, {TOOL: print}, {1: None}])
def test_bad_tools(tools):
    with pytest.raises(OversightError, match="ReviewedTool"):
        ReviewTools(tools, run_id=run_id)


def test_bad_settings(make, offline):
    world = World(make, PydanticHarness)
    with pytest.raises(OversightError, match="run_id"):
        ReviewTools({TOOL: world.tool}, run_id="run-1")
    review = ReviewTools({TOOL: world.tool}, run_id=run_id)
    with pytest.raises(OversightError, match="no reviewed tool"):
        review.tool("other", description="o", parameters_json_schema=SCHEMA)


# ----------------------------------------------------------------- the guide


def test_the_python_in_the_pydantic_ai_guide_runs(tmp_path, monkeypatch, offline):
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)
    text = (ROOT / "docs" / "guides" / "pydantic-ai.md").read_text(encoding="utf-8")
    [snippet] = re.findall(r"^```python\n(.*?)^```$", text, flags=re.MULTILINE | re.DOTALL)
    deviation = {**ITEMS[0], "record_id": "QMS-601"}
    model = FunctionModel(Script(calls([deviation], name="save_deviation")))
    namespace = {"model": model}
    exec(compile(snippet, "pydantic-ai.md", "exec"), namespace)  # noqa: S102
    assert namespace["written"] == ["QMS-601"]
