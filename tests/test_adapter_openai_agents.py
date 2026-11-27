"""The OpenAI Agents SDK adapter, with the real SDK and a scripted model.

Skipped unless the SDK is installed: ``pip install -e ".[dev,openai-agents]"``.
"""

import asyncio
import dataclasses
import json
import re
import shutil
from pathlib import Path

import pytest

pytest.importorskip("agents")

from agents import (
    Agent,
    FunctionTool,
    Runner,
    RunState,
    ToolGuardrailFunctionOutput,
    Usage,
    set_tracing_disabled,
    tool_output_guardrail,
)
from agents.exceptions import UserError
from agents.items import ModelResponse
from agents.models.interface import Model
from conformance import PROPOSAL, SECOND, AdapterConformance, Harness, World
from deploy import ITEMS
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from reviewproof import OversightError, TransitionError
from reviewproof.adapters.openai_agents import ReviewTools, ToolStopped
from reviewproof.adapters.tool import NOT_APPROVED

# The SDK sends traces to its vendor unless told not to.
set_tracing_disabled(True)

ROOT = Path(__file__).parents[1]
TOOL = "write_record"
SCHEMA = {"type": "object", "properties": {"record_id": {"type": "string"}}}


class ScriptedModel(Model):
    """First asks for ``turns[0]`` calls, and so on. Then answers in text."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.asked = 0
        self.given = []

    async def get_response(self, *args, **kwargs):
        history = args[1] if len(args) > 1 else kwargs["input"]
        self.asked += 1
        self.given = [
            item["output"]
            for item in history
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        if self.turns:
            output = [
                ResponseFunctionToolCall(
                    type="function_call", call_id=call_id, name=name, arguments=arguments
                )
                for call_id, name, arguments in self.turns.pop(0)
            ]
        else:
            text = ResponseOutputText(type="output_text", text="done", annotations=[])
            output = [
                ResponseOutputMessage(
                    id="m", type="message", role="assistant", status="completed", content=[text]
                )
            ]
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


def calls(proposals, start=0, name=TOOL):
    return [(f"call-{n}", name, json.dumps(p)) for n, p in enumerate(proposals, start)]


def run_id(context):
    return context["run_id"]


def run_the_sdk(coroutine):
    """The SDK wraps a tool's error. Hand the tests ReviewProof's own, so they can name it."""
    try:
        return asyncio.run(coroutine)
    except UserError as error:
        if isinstance(error.__cause__, OversightError):
            raise error.__cause__ from error
        raise


class OpenAIHarness(Harness):
    key = json.dumps(["run-1", "call-0"])

    def __init__(self, tool, *proposals):
        super().__init__(tool, *proposals)
        self.model = ScriptedModel(calls(proposals))
        self.saved = self.at = self.review = self.agent = None
        self.shown = ""

    def build(self, **changes):
        # Built late: a test may swap the tool before the agent starts.
        self.review = ReviewTools({TOOL: self.tool}, run_id=run_id)
        function = self.review.function_tool(
            TOOL, description="Write.", params_json_schema=SCHEMA, strict_json_schema=False
        )
        if changes:
            function = dataclasses.replace(function, **changes)
        self.agent = Agent(name="writer", model=self.model, tools=[function])
        return self.agent

    def _keep(self, result):
        self.review.submit(result)
        self.saved = result.to_state().to_json()
        self.shown = repr(result.interruptions) + repr(result.new_items)

    async def _restore(self):
        return await RunState.from_json(self.agent, json.loads(json.dumps(self.at or self.saved)))

    def start(self):
        agent = self.build()
        result = run_the_sdk(Runner.run(agent, "go", context={"run_id": "run-1"}))
        assert result.interruptions
        self._keep(result)

    def saved_state(self):
        return json.dumps(self.saved) + self.shown

    def resume(self):
        async def go():
            state = await self._restore()
            assert self.review.apply_decisions(state, "run-1")
            return await Runner.run(self.agent, state)

        self._keep(run_the_sdk(go()))

    def force_resume(self):
        async def go():
            state = await self._restore()
            for item in state.get_interruptions():
                state.approve(item)
            return await Runner.run(self.agent, state)

        run_the_sdk(go())

    def run_without_pause(self):
        agent = self.build(needs_approval=False)
        run_the_sdk(Runner.run(agent, "go", context={"run_id": "run-1"}))

    def snapshot(self):
        return json.loads(json.dumps(self.saved))

    def restore(self, snapshot):
        self.at = snapshot

    def replies(self):
        return list(self.model.given)

    def model_calls(self):
        return self.model.asked


class TestOpenAIAgentsAdapter(AdapterConformance):
    harness = OpenAIHarness


# ----------------------------------------------------------------- this SDK alone


def started(make, *proposals):
    world = World(make, OpenAIHarness, proposals or (PROPOSAL,))
    world.agent.start()
    return world


def decide_and_resume(world, proposal=PROPOSAL, decision="approve"):
    world.decide(world.real_token(proposal), decision)
    world.agent.resume()


def test_the_tool_needs_approval_and_has_no_time_limit(make, offline):
    world = World(make, OpenAIHarness)
    function = world.agent.build().tools[0]
    assert isinstance(function, FunctionTool)
    assert function.needs_approval is True
    assert function.timeout_seconds is None


def test_a_standing_approval_cannot_write_what_was_not_reviewed(make, offline):
    """``always_approve`` lets the next call run with no pause. The guard stops it."""
    world = World(make, OpenAIHarness)
    world.agent.model = ScriptedModel(calls([PROPOSAL]), calls([SECOND], start=1))
    world.agent.start()
    world.decide(world.real_token(PROPOSAL))

    async def go():
        state = await world.agent._restore()
        [item] = state.get_interruptions()
        state.approve(item, always_approve=True)
        return await Runner.run(world.agent.agent, state)

    with pytest.raises(OversightError, match="nothing in this queue"):
        run_the_sdk(go())
    assert world.written == [PROPOSAL]
    assert world.agent.model_calls() == 2


def test_a_later_call_goes_to_review_in_turn(make, offline):
    world = World(make, OpenAIHarness)
    world.agent.model = ScriptedModel(calls([PROPOSAL]), calls([SECOND], start=1))
    world.agent.start()
    decide_and_resume(world, PROPOSAL)
    assert world.written == [PROPOSAL]
    decide_and_resume(world, SECOND)
    assert world.written == [PROPOSAL, SECOND]
    assert world.agent.model_calls() == 3


def test_nothing_to_apply_until_a_decision(make, offline):
    world = started(make, PROPOSAL, SECOND)

    async def apply():
        state = await world.agent._restore()
        return world.agent.review.apply_decisions(state, "run-1"), state

    applied, state = asyncio.run(apply())
    assert not applied and len(state.get_interruptions()) == 2
    world.decide(world.real_token(SECOND))
    applied, _ = asyncio.run(apply())
    assert applied


def test_a_rejected_call_is_rejected_in_the_sdk_and_the_tool_never_runs(make, offline, monkeypatch):
    """The shared code would write nothing either way. This keeps the first line of defence."""
    world = started(make)
    invoked = []
    run = world.tool.run
    monkeypatch.setattr(world.tool, "run", lambda *a: invoked.append(a) or run(*a))
    decide_and_resume(world, decision="reject")
    assert invoked == [] and world.written == []
    assert world.agent.replies() == [NOT_APPROVED]


def test_the_wrong_run_id_applies_nothing(make, offline):
    world = started(make)
    world.decide(world.real_token(PROPOSAL))

    async def apply():
        state = await world.agent._restore()
        return world.agent.review.apply_decisions(state, "another-run")

    with pytest.raises(OversightError, match="nothing in this queue"):
        asyncio.run(apply())
    assert world.written == []


def test_resuming_from_an_older_state_writes_nothing_twice(make, offline):
    world = started(make, PROPOSAL, SECOND)
    first = world.agent.snapshot()
    decide_and_resume(world, SECOND)
    world.agent.restore(first)
    world.decide(world.real_token(PROPOSAL))
    with pytest.raises(TransitionError, match="already been requested"):
        world.agent.resume()
    targets = [a["record_id"] for a in world.written]
    assert sorted(targets) == sorted(set(targets))


def test_submitting_again_creates_nothing_new(make, offline):
    world = World(make, OpenAIHarness)
    agent = world.agent.build()
    result = asyncio.run(Runner.run(agent, "go", context={"run_id": "run-1"}))
    world.agent.review.submit(result)
    tickets, events = set(world.queue), world.events()
    world.agent.review.submit(result)
    assert (set(world.queue), world.events()) == (tickets, events)


@tool_output_guardrail
def block_the_result(data):
    return ToolGuardrailFunctionOutput.reject_content("The result was blocked.")


UNSAFE = [
    ({"timeout_seconds": 5.0}, "time limit"),
    ({"tool_output_guardrails": [block_the_result]}, "output guardrail"),
]


@pytest.mark.parametrize(("changes", "message"), UNSAFE, ids=["time-limit", "guardrail"])
def test_an_unsafe_setting_is_refused_at_the_pause(make, offline, changes, message):
    world = World(make, OpenAIHarness)
    agent = world.agent.build(**changes)
    result = asyncio.run(Runner.run(agent, "go", context={"run_id": "run-1"}))
    with pytest.raises(OversightError, match=message):
        world.agent.review.submit(result)
    assert world.queue == {}


@pytest.mark.parametrize(("changes", "message"), UNSAFE, ids=["time-limit", "guardrail"])
def test_an_unsafe_setting_added_after_the_pause_writes_nothing(make, offline, changes, message):
    """As when a second process builds the agent differently before resuming."""
    world = started(make)
    world.decide(world.real_token(PROPOSAL))
    world.agent.build(**changes)
    with pytest.raises(OversightError, match=message):
        world.agent.resume()
    assert world.written == []
    assert world.agent.model_calls() == 1


def test_a_write_cancelled_from_inside_stops_the_run(make, offline):
    """A hand-built tool's cancellation would otherwise become a message for the model."""
    world = started(make)
    world.decide(world.real_token(PROPOSAL))
    world.failure = asyncio.CancelledError()
    with pytest.raises(UserError) as stopped:
        world.agent.resume()
    assert isinstance(stopped.value.__cause__, ToolStopped)
    assert world.agent.model_calls() == 1
    assert len(world.written) == 1


def test_calls_to_other_tools_are_left_alone(make, offline):
    world = World(make, OpenAIHarness)
    review = ReviewTools({TOOL: world.tool}, run_id=run_id)

    async def other(context, arguments):
        return "ok"

    plain = FunctionTool(
        name="other",
        description="o",
        params_json_schema=SCHEMA,
        strict_json_schema=False,
        on_invoke_tool=other,
        needs_approval=True,
    )
    model = ScriptedModel(calls([{}], name="other"))
    agent = Agent(name="a", model=model, tools=[plain])
    result = asyncio.run(Runner.run(agent, "go", context={}))
    review.submit(result)
    assert not review.apply_decisions(result.to_state(), "run-1")
    assert world.queue == {}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [("not json", "not JSON"), ("[1]", "must be an object")],
)
def test_arguments_that_cannot_be_reviewed(make, offline, arguments, message):
    world = World(make, OpenAIHarness)
    world.agent.model = ScriptedModel(
        [("call-0", TOOL, json.dumps(PROPOSAL)), ("call-1", TOOL, arguments)]
    )
    agent = world.agent.build()
    result = asyncio.run(Runner.run(agent, "go", context={"run_id": "run-1"}))
    with pytest.raises(OversightError, match=message):
        world.agent.review.submit(result)
    assert world.queue == {}, "no call is submitted if any cannot be"


@pytest.mark.parametrize("context", [{"run_id": ""}, {"run_id": 7}])
def test_a_run_needs_an_id(make, offline, context):
    world = World(make, OpenAIHarness)
    agent = world.agent.build()
    result = asyncio.run(Runner.run(agent, "go", context=context))
    with pytest.raises(OversightError, match="run ID"):
        world.agent.review.submit(result)
    assert world.queue == {}


@pytest.mark.parametrize("tools", [{}, None, {TOOL: print}, {1: None}])
def test_bad_tools(tools):
    with pytest.raises(OversightError, match="ReviewedTool"):
        ReviewTools(tools, run_id=run_id)


def test_bad_settings(make, offline):
    world = World(make, OpenAIHarness)
    with pytest.raises(OversightError, match="run_id"):
        ReviewTools({TOOL: world.tool}, run_id="run-1")
    review = ReviewTools({TOOL: world.tool}, run_id=run_id)
    with pytest.raises(OversightError, match="no reviewed tool"):
        review.function_tool("other", description="o", params_json_schema=SCHEMA)


# ----------------------------------------------------------------- the guide


def test_the_python_in_the_openai_guide_runs(tmp_path, monkeypatch, offline):
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)
    text = (ROOT / "docs" / "guides" / "openai-agents.md").read_text(encoding="utf-8")
    [snippet] = re.findall(r"^```python\n(.*?)^```$", text, flags=re.MULTILINE | re.DOTALL)
    deviation = {**ITEMS[0], "record_id": "QMS-501"}
    namespace = {"model": ScriptedModel(calls([deviation], name="save_deviation"))}
    exec(compile(snippet, "openai-agents.md", "exec"), namespace)  # noqa: S102
    assert namespace["written"] == ["QMS-501"]
