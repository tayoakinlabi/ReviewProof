"""The LangGraph adapter, with the real library and a scripted model.

Skipped unless LangGraph is installed: ``pip install -e ".[dev,langgraph]"``.
"""

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Annotated, TypedDict

import pytest

pytest.importorskip("langgraph")

from conformance import PROPOSAL, SECOND, AdapterConformance, Harness, World
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, RetryPolicy, interrupt

from reviewproof import OversightError, TransitionError
from reviewproof.adapters.langgraph import PAUSE, ReviewStep
from reviewproof.adapters.tool import ReviewPending

ROOT = Path(__file__).parents[1]
TOOL = "write_record"
THIRD = {**SECOND, "record_id": "QMS-REC-0003", "deviation_id": "DEV-2025-7003"}


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(name, raising=False)


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def tool_calls(proposals, name=TOOL):
    return [
        {"name": name, "args": dict(p), "id": f"call-{n}", "type": "tool_call"}
        for n, p in enumerate(proposals)
    ]


class LangGraphHarness(Harness):
    """A graph of a scripted model and ``ReviewStep``."""

    thread = "thread-1"
    key = json.dumps(["thread-1", "call-0"])

    def __init__(self, tool, *proposals, retry=None):
        super().__init__(tool, *proposals)
        self.retry = retry
        self.saver = InMemorySaver()
        self.asked = 0
        self.config = {"configurable": {"thread_id": self.thread}}
        self.at = None
        self.app = self.step = None
        self.returned = ""

    def build(self):
        # Built late: a test may swap the tool before the agent starts.
        self.step = ReviewStep({TOOL: self.tool})

        def model(state):
            self.asked += 1
            if any(isinstance(m, ToolMessage) for m in state["messages"]):
                return {"messages": [AIMessage(content="done")]}
            return {"messages": [AIMessage(content="", tool_calls=tool_calls(self.proposals))]}

        def route(state):
            return self.step.sends("review", state["messages"][-1].tool_calls) or END

        graph = StateGraph(State)
        graph.add_node("model", model)
        graph.add_node("review", self.step, retry_policy=self.retry)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", route, ["review", END])
        graph.add_edge("review", "model")
        self.app = graph.compile(checkpointer=self.saver)
        return self.app

    def start(self):
        result = self.build().invoke({"messages": [("user", "go")]}, self.config)
        assert result["__interrupt__"]
        self.returned = repr(result)

    def saved_state(self):
        saved = [self.saver.storage, self.saver.writes, self.saver.blobs]
        return repr(saved) + repr(self.app.get_state(self.config)) + self.returned

    def resume(self):
        command = self.step.resume_command(self.app, self.config)
        assert command is not None
        self.app.invoke(command, self.config)

    def force_resume(self):
        config = self.at or self.config
        pending = self.app.get_state(config).interrupts
        self.app.invoke(Command(resume={p.id: True for p in pending}), config)

    def run_without_pause(self):
        graph = StateGraph(State)
        graph.add_node("act", lambda state: self.tool.run(self.key, self.arguments) and {})
        graph.add_edge(START, "act")
        graph.compile(checkpointer=InMemorySaver()).invoke({"messages": []}, self.config)

    def snapshot(self):
        return self.app.get_state(self.config).config

    def restore(self, snapshot):
        self.at = snapshot

    def replies(self):
        messages = self.app.get_state(self.config).values["messages"]
        return [m.content for m in messages if isinstance(m, ToolMessage)]

    def model_calls(self):
        return self.asked


class TestLangGraphAdapter(AdapterConformance):
    harness = LangGraphHarness


# ----------------------------------------------------------------- LangGraph alone


def started(make, *proposals, **options):
    world = World(make, LangGraphHarness, proposals or (PROPOSAL,))
    world.agent.retry = options.get("retry")
    world.agent.start()
    return world


def pending(agent):
    return agent.app.get_state(agent.config).interrupts


def test_the_pause_names_the_action_and_nothing_more(make, offline):
    world = started(make)
    [pause] = pending(world.agent)
    assert pause.value == {"reviewproof": {"status": PAUSE, "tool": TOOL, "key": world.agent.key}}


def test_all_calls_pause_at_once_and_are_decided_in_any_order(make, offline):
    proposals = (PROPOSAL, SECOND, THIRD)
    world = started(make, *proposals)
    assert len(pending(world.agent)) == 3
    for done, proposal in enumerate(reversed(proposals), start=1):
        world.decide(world.real_token(proposal))
        world.agent.resume()
        assert len(world.written) == done
        assert len(pending(world.agent)) == 3 - done
    assert world.written == [THIRD, SECOND, PROPOSAL]
    replies = [
        m
        for m in world.agent.app.get_state(world.agent.config).values["messages"]
        if isinstance(m, ToolMessage)
    ]
    assert {m.tool_call_id for m in replies} == {"call-0", "call-1", "call-2"}
    assert world.agent.model_calls() == 2


def test_a_pause_is_offered_for_resuming_once_it_is_decided_and_only_then(make, offline):
    world = started(make, PROPOSAL, SECOND)
    step, app, config = world.agent.step, world.agent.app, world.agent.config
    assert step.resume_command(app, config) is None
    world.decide(world.real_token(PROPOSAL))
    command = step.resume_command(app, config)
    assert len(command.resume) == 1
    app.invoke(command, config)
    assert step.resume_command(app, config) is None
    # Deciding a planted item of a call already resumed offers nothing.
    planted = [
        t for t in world.queue if t not in (world.real_token(PROPOSAL), world.real_token(SECOND))
    ]
    for token in planted:
        world.decide(token)
    assert step.resume_command(app, config) is None
    assert world.written == [PROPOSAL]


def test_what_langgraph_returns_on_resume_changes_nothing(make, offline):
    world = started(make)
    world.decide(world.real_token(PROPOSAL))
    [pause] = pending(world.agent)
    world.agent.app.invoke(Command(resume={pause.id: False}), world.agent.config)
    assert world.written == [PROPOSAL]


def test_other_pauses_are_left_alone(make, offline):
    world = World(make, LangGraphHarness)
    step = ReviewStep({TOOL: world.tool})
    graph = StateGraph(State)
    graph.add_node("ask", lambda state: interrupt({"question": "continue?"}) and {})
    graph.add_edge(START, "ask")
    app = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t"}}
    app.invoke({"messages": []}, config)
    assert step.resume_command(app, config) is None


@pytest.mark.parametrize("failing", [PROPOSAL, SECOND], ids=["first", "second"])
def test_after_a_failure_running_again_repeats_no_write(make, offline, failing):
    world = started(make, PROPOSAL, SECOND, THIRD)
    for proposal in (PROPOSAL, SECOND, THIRD):
        world.decide(world.real_token(proposal))

    def write(**arguments):
        world.written.append(arguments)
        if arguments["record_id"] == failing["record_id"]:
            raise ConnectionError("down")
        return "record saved"

    world.tool._write = write
    with pytest.raises(ConnectionError):
        world.agent.resume()
    # A host that simply runs the graph again is stopped, and nothing is written twice.
    with pytest.raises(TransitionError, match="already been requested"):
        world.agent.app.invoke(None, world.agent.config)
    targets = [a["record_id"] for a in world.written]
    assert len(targets) == len(set(targets))


def test_a_retry_policy_cannot_write_twice(make, offline):
    world = World(make, LangGraphHarness)
    world.agent.retry = RetryPolicy(max_attempts=3, initial_interval=0.01, jitter=False)
    world.agent.start()
    world.decide(world.real_token(PROPOSAL))
    world.failure = ConnectionError("down")
    with pytest.raises(TransitionError, match="already been requested"):
        world.agent.resume()
    assert len(world.written) == 1
    assert world.agent.model_calls() == 1


def test_a_repeated_call_id_with_other_content_is_refused(make, offline):
    """A model that reuses a call ID in one thread gets a refusal, not an unreviewed write."""
    world = started(make)
    world.decide(world.real_token(PROPOSAL))
    world.agent.resume()
    step = world.agent.step
    graph = StateGraph(State)
    graph.add_node("review", step)
    graph.add_conditional_edges(
        START, lambda s: step.sends("review", tool_calls([SECOND])), ["review"]
    )
    app = graph.compile(checkpointer=InMemorySaver())
    with pytest.raises(OversightError, match="already used"):
        app.invoke({"messages": []}, world.agent.config)
    assert world.written == [PROPOSAL]


def test_giving_the_step_the_graph_state_submits_nothing(make, offline):
    world = World(make, LangGraphHarness)
    graph = StateGraph(State)
    graph.add_node("review", ReviewStep({TOOL: world.tool}))
    graph.add_edge(START, "review")
    app = graph.compile(checkpointer=InMemorySaver())
    message = AIMessage(content="", tool_calls=tool_calls([PROPOSAL, SECOND]))
    with pytest.raises(OversightError, match="takes one tool call"):
        app.invoke({"messages": [message]}, {"configurable": {"thread_id": "t"}})
    assert world.queue == {} and world.written == []


@pytest.mark.parametrize(
    ("change", "message"),
    [({"name": "delete_everything"}, "no reviewed tool"), ({"id": ""}, "has no ID")],
)
def test_a_call_the_step_cannot_take(make, offline, change, message):
    world = World(make, LangGraphHarness)
    step = ReviewStep({TOOL: world.tool})
    with pytest.raises(OversightError, match=message):
        step({**tool_calls([PROPOSAL])[0], **change})
    assert world.queue == {}


def test_a_run_without_a_thread_id_submits_nothing(make, offline):
    world = World(make, LangGraphHarness)
    step = ReviewStep({TOOL: world.tool})
    graph = StateGraph(State)
    graph.add_node("review", step)
    graph.add_conditional_edges(
        START, lambda s: step.sends("review", tool_calls([PROPOSAL])), ["review"]
    )
    with pytest.raises(OversightError, match="thread_id"):
        graph.compile().invoke({"messages": []})
    assert world.queue == {}


def test_only_calls_to_reviewed_tools_are_sent(make, offline):
    world = World(make, LangGraphHarness)
    step = ReviewStep({TOOL: world.tool})
    calls = tool_calls([PROPOSAL]) + tool_calls([SECOND], name="read_record")
    sends = step.sends("review", calls)
    assert [(s.node, s.arg["name"]) for s in sends] == [("review", TOOL)]


@pytest.mark.parametrize("tools", [{}, None, {TOOL: print}, {1: None}])
def test_bad_tools(tools):
    with pytest.raises(OversightError, match="ReviewedTool"):
        ReviewStep(tools)


class _Odd:
    def __str__(self):
        return "an odd result"


def _circle():
    value = []
    value.append(value)
    return value


@pytest.mark.parametrize(
    ("result", "text"),
    [
        ({"saved": True, "by": "é"}, '{"saved": true, "by": "é"}'),
        (_Odd(), '"an odd result"'),
        (_circle(), "[[...]]"),
        (None, "null"),
    ],
)
def test_any_result_of_a_finished_write_reaches_the_agent_as_text(make, offline, result, text):
    world = started(make)
    world.decide(world.real_token(PROPOSAL))
    world.tool._write = lambda **arguments: result
    world.agent.resume()
    assert world.agent.replies() == [text]


@pytest.mark.skipif(sys.version_info >= (3, 11), reason="LangGraph's limit is on 3.10")
def test_on_python_310_ainvoke_is_refused_before_anything_is_submitted(make, offline):
    world = World(make, LangGraphHarness)
    agent = world.agent
    app = agent.build()
    with pytest.raises(OversightError, match="invoke, not ainvoke"):
        asyncio.run(app.ainvoke({"messages": [("user", "go")]}, agent.config))
    assert world.queue == {} and world.written == []


@pytest.mark.skipif(sys.version_info < (3, 11), reason="LangGraph needs Python 3.11 for this")
def test_it_works_with_ainvoke(make, offline):
    world = World(make, LangGraphHarness, (PROPOSAL, SECOND))
    agent = world.agent
    app = agent.build()
    asyncio.run(app.ainvoke({"messages": [("user", "go")]}, agent.config))
    for proposal in (PROPOSAL, SECOND):
        world.decide(world.real_token(proposal))
    command = agent.step.resume_command(app, agent.config)
    asyncio.run(app.ainvoke(command, agent.config))
    assert sorted(a["record_id"] for a in world.written) == [
        PROPOSAL["record_id"],
        SECOND["record_id"],
    ]


def test_resuming_early_can_be_recovered_from(make, offline):
    world = started(make)
    with pytest.raises(ReviewPending):
        world.agent.force_resume()
    world.decide(world.real_token(PROPOSAL))
    world.agent.app.invoke(None, world.agent.config)
    assert world.written == [PROPOSAL]


# ----------------------------------------------------------------- the guide


def test_the_python_in_the_langgraph_guide_runs(tmp_path, monkeypatch, offline):
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)
    text = (ROOT / "docs" / "guides" / "langgraph.md").read_text(encoding="utf-8")
    [snippet] = re.findall(r"^```python\n(.*?)^```$", text, flags=re.MULTILINE | re.DOTALL)
    namespace = {}
    exec(compile(snippet, "langgraph.md", "exec"), namespace)  # noqa: S102
    assert namespace["written"] == ["QMS-401"]
