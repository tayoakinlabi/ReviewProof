"""The Strands adapter, with the real library and a scripted model.

Skipped unless Strands Agents is installed: ``pip install -e ".[dev,strands]"``.
"""

import asyncio
import json
import re
import shutil
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("strands")

from conformance import PROPOSAL, SECOND, AdapterConformance, Harness, World
from deploy import ITEMS
from strands import Agent
from strands.hooks import AfterToolCallEvent, HookProvider
from strands.models.model import Model
from strands.session.file_session_manager import FileSessionManager
from strands.tools import PythonAgentTool
from strands.tools.executors import ConcurrentToolExecutor, SequentialToolExecutor

from reviewproof import GuardError, OversightError, TransitionError
from reviewproof.adapters.strands import FAILED, PAUSE, STOPPING, ReviewTools, RunStopped
from reviewproof.adapters.tool import NOT_APPROVED

ROOT = Path(__file__).parents[1]
TOOL = "write_record"
SCHEMA = {"type": "object", "properties": {"record_id": {"type": "string"}}}


def calls(proposals, start=0, name=TOOL):
    return [(f"call-{n}", name, dict(p)) for n, p in enumerate(proposals, start)]


class ScriptedModel(Model):
    """First asks for ``turns[0]``, and so on. Then it answers in text."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.asked = 0
        self.told = []

    def update_config(self, **config):
        pass

    def get_config(self):
        return {}

    def structured_output(self, *args, **kwargs):
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.asked += 1
        self.told = [
            block["toolResult"]["content"][0]["text"]
            for message in messages
            for block in message["content"]
            if "toolResult" in block
        ]
        yield {"messageStart": {"role": "assistant"}}
        if self.turns:
            for call_id, name, arguments in self.turns.pop(0):
                start = {"toolUse": {"toolUseId": call_id, "name": name}}
                yield {"contentBlockStart": {"start": start}}
                delta = {"toolUse": {"input": json.dumps(arguments)}}
                yield {"contentBlockDelta": {"delta": delta}}
                yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "done"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}


def run_id(agent):
    return agent.state.get("run_id")


def call_agent(agent, prompt):
    """Hand the tests ReviewProof's own errors, so that they can name them."""
    try:
        return agent(prompt)
    except RunStopped as stopped:
        if isinstance(stopped.__cause__, OversightError):
            raise stopped.__cause__ from None
        raise


def answers(interrupts):
    return [{"interruptResponse": {"interruptId": i.id, "response": True}} for i in interrupts]


class StrandsHarness(Harness):
    """The agent's session is saved to files, as a host that restarts would keep it."""

    key = json.dumps(["run-1", "call-0"])

    def __init__(self, tool, *proposals):
        super().__init__(tool, *proposals)
        self.model = ScriptedModel(calls(proposals))
        self.folder = tempfile.mkdtemp()
        self.review = self.agent = None
        self.interrupts = []

    def build(
        self,
        folder=None,
        review_class=ReviewTools,
        hooks=(),
        raw_tool=None,
        with_hooks=True,
        **options,
    ):
        # Built late: a test may swap the tool before the agent starts.
        self.review = review_class({TOOL: self.tool}, run_id=run_id)
        function = self.review.tool(TOOL, description="Write.", input_schema=SCHEMA)
        self.agent = Agent(
            model=self.model,
            tools=[raw_tool or function],
            hooks=[self.review, *hooks] if with_hooks else list(hooks),
            session_manager=FileSessionManager(session_id="s", storage_dir=folder or self.folder),
            agent_id="writer",
            state={"run_id": "run-1"},
            callback_handler=None,
            **options,
        )
        return self.agent

    def _keep(self, result):
        self.interrupts = list(result.interrupts or [])

    def start(self, **build):
        self.build(**build)
        result = call_agent(self.agent, "go")
        assert result.stop_reason == "interrupt"
        self._keep(result)

    def saved_state(self):
        files = [p.read_text("utf-8") for p in Path(self.folder).rglob("*.json")]
        handed_back = [(i.name, i.reason) for i in self.interrupts]
        return " ".join(files) + json.dumps(handed_back)

    def resume(self):
        ready = self.review.resume_input(self.interrupts)
        if ready is not None:
            self._keep(call_agent(self.agent, ready))

    def force_resume(self):
        self._keep(call_agent(self.agent, answers(self.interrupts)))

    def run_without_pause(self):
        class NoPause(ReviewTools):
            def _before_tool(self, event):
                event.agent._reviewproof_paused = {event.tool_use["toolUseId"]}

        self.build(review_class=NoPause)
        call_agent(self.agent, "go")

    def snapshot(self):
        saved = tempfile.mkdtemp()
        shutil.copytree(self.folder, saved, dirs_exist_ok=True)
        return saved, list(self.interrupts)

    def restore(self, snapshot):
        saved, interrupts = snapshot
        self.folder = tempfile.mkdtemp()
        shutil.copytree(saved, self.folder, dirs_exist_ok=True)
        self.interrupts = list(interrupts)
        self.build()

    def replies(self):
        return list(self.model.told)

    def model_calls(self):
        return self.model.asked


class TestStrandsAdapter(AdapterConformance):
    harness = StrandsHarness


# ----------------------------------------------------------------- this library alone


def started(make, *proposals, **build):
    world = World(make, StrandsHarness, proposals or (PROPOSAL,))
    world.agent.start(**build)
    return world


def approve(world, proposal=PROPOSAL):
    world.decide(world.real_token(proposal))


def history(agent):
    return json.dumps(agent.messages)


def test_the_pause_names_the_action_and_nothing_more(make, offline):
    world = started(make)
    [pause] = world.agent.interrupts
    assert (pause.name, pause.reason) == (
        "reviewproof",
        {"reviewproof": {"status": PAUSE, "tool": TOOL, "key": world.agent.key}},
    )


def _raiser(error):
    def go():
        raise error

    return go


class _Outside(BaseException):
    pass


@pytest.mark.parametrize("executor", [ConcurrentToolExecutor, SequentialToolExecutor])
@pytest.mark.parametrize(
    "error",
    [ConnectionError("secret detail"), _Outside("secret detail"), asyncio.CancelledError()],
    ids=["ordinary", "outside-exception", "cancelled"],
)
def test_no_failure_reaches_the_model_or_is_lost(make, offline, executor, error):
    world = started(make, tool_executor=executor())
    approve(world)
    world.failure = error
    with pytest.raises(RunStopped) as stopped:
        world.agent.resume()
    assert stopped.value.__cause__ is error
    assert world.agent.model_calls() == 1
    assert len(world.written) == 1
    assert "secret detail" not in history(world.agent.agent)


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_a_request_to_stop_the_program_is_passed_on(make, offline, error):
    world = World(make, StrandsHarness)
    review = ReviewTools({TOOL: world.tool}, run_id=run_id)
    function = review.tool(TOOL, description="w", input_schema=SCHEMA)
    agent = Agent(model=ScriptedModel(), tools=[function], callback_handler=None)
    agent._reviewproof_paused = {"c"}

    def run(*arguments):
        raise error

    world.tool.run = run
    agent.state.set("run_id", "r")
    with pytest.raises(error):
        function._tool_func({"toolUseId": "c", "input": PROPOSAL}, agent=agent)
    assert agent.state.get(FAILED) is True


def test_a_failure_while_another_call_waits_still_stops_the_run(make, offline):
    """The run would pause again without asking the model, and look like any other pause."""
    world = started(make, PROPOSAL, SECOND)
    approve(world)
    world.failure = ConnectionError("down")
    with pytest.raises(RunStopped):
        world.agent.resume()
    assert world.agent.model_calls() == 1
    # The call that was waiting cannot write through the failed agent, even once approved.
    world.failure = None
    approve(world, SECOND)
    with pytest.raises((RunStopped, OversightError)):
        call_agent(world.agent.agent, answers(world.agent.interrupts))
    assert [a["record_id"] for a in world.written] == [PROPOSAL["record_id"]]
    assert world.agent.model_calls() == 1


def test_a_failed_agent_stays_stopped_and_so_does_its_saved_session(make, offline):
    world = started(make)
    approve(world)
    world.failure = ConnectionError("down")
    with pytest.raises(RunStopped):
        world.agent.resume()
    with pytest.raises(RunStopped):
        world.agent.agent("carry on")
    world.agent.build()  # as after a restart, from the saved session
    assert world.agent.agent.state.get(FAILED) is True
    with pytest.raises(RunStopped):
        world.agent.agent("carry on")
    assert world.agent.model_calls() == 1
    assert len(world.written) == 1


def test_a_restart_from_a_session_that_went_back_to_the_pause_writes_nothing_twice(make, offline):
    """When a failure stops a run that was about to pause again, Strands' saved session can
    be the one from the earlier pause, which does not remember the failure.
    """
    world = started(make, PROPOSAL, SECOND)
    saved = world.agent.snapshot()
    approve(world)
    world.failure = ConnectionError("down")
    with pytest.raises(RunStopped):
        world.agent.resume()
    world.failure = None
    world.agent.restore(saved)
    with pytest.raises(TransitionError, match="already been requested"):
        world.agent.resume()
    assert len(world.written) == 1
    assert world.agent.model_calls() == 1


class _EndTheLoop(HookProvider):
    def register_hooks(self, registry, **kwargs):
        registry.add_callback(AfterToolCallEvent, self.after)

    def after(self, event):
        event.invocation_state.setdefault("request_state", {})["stop_event_loop"] = True


def test_a_failure_stops_the_run_even_if_the_model_would_not_be_asked_again(make, offline):
    world = started(make, hooks=[_EndTheLoop()])
    approve(world)
    world.failure = ConnectionError("down")
    with pytest.raises(RunStopped):
        world.agent.resume()


class _RetryTwice(HookProvider):
    def __init__(self):
        self.retries = 0

    def register_hooks(self, registry, **kwargs):
        registry.add_callback(AfterToolCallEvent, self.after)

    def after(self, event):
        if self.retries < 2:
            self.retries += 1
            event.retry = True


@pytest.mark.parametrize("fails", [True, False], ids=["after-a-failure", "after-success"])
def test_a_retry_hook_cannot_write_twice(make, offline, fails):
    retry = _RetryTwice()
    world = started(make, hooks=[retry])
    approve(world)
    world.failure = ConnectionError("down") if fails else None
    with pytest.raises((RunStopped, TransitionError)):
        world.agent.resume()
    assert retry.retries >= 1
    assert len(world.written) == 1
    assert world.agent.model_calls() == 1


def test_without_the_hooks_the_tool_writes_nothing(make, offline):
    world = World(make, StrandsHarness)
    review = ReviewTools({TOOL: world.tool}, run_id=run_id)
    model = ScriptedModel(calls([PROPOSAL]))
    agent = Agent(
        model=model,
        tools=[review.tool(TOOL, description="w", input_schema=SCHEMA)],
        state={"run_id": "run-1"},
        callback_handler=None,
    )
    # With no hooks nothing can stop the run, so the model is told that the call failed.
    agent("go")
    assert world.written == [] and world.queue == {}
    assert model.told == [STOPPING]


def test_an_approved_call_does_not_write_on_an_agent_rebuilt_without_the_hooks(make, offline):
    """Approved and submitted, so only the missing pause check stands in the way. Writing here
    would mean writing with nothing able to stop the run if the write failed.
    """
    world = started(make)
    approve(world)
    world.agent.build(with_hooks=False)
    world.agent.agent(answers(world.agent.interrupts))
    assert world.written == []
    assert world.agent.replies() == [STOPPING]


def test_another_tool_under_a_reviewed_name_is_never_run(make, offline):
    """A host's own function registered under the name would write with no guard."""
    world = World(make, StrandsHarness)
    unguarded = []

    def raw(tool_use, **state):
        unguarded.append(tool_use)
        return {"toolUseId": tool_use["toolUseId"], "status": "success", "content": []}

    spec = {"name": TOOL, "description": "raw", "inputSchema": {"json": SCHEMA}}
    world.agent.build(raw_tool=PythonAgentTool(TOOL, spec, raw))
    with pytest.raises(OversightError, match="not built by review.tool"):
        call_agent(world.agent.agent, "go")
    assert unguarded == [] and world.queue == {}
    assert world.agent.model_calls() == 1


@pytest.mark.parametrize("when", ["from-the-start", "added-after-the-pause"])
def test_background_tasks_are_refused(make, offline, when):
    """With them the model is answered before the write finishes."""
    world = World(make, StrandsHarness)
    if when == "from-the-start":
        world.agent.build(background_tasks=True)
        with pytest.raises(OversightError, match="background tasks"):
            call_agent(world.agent.agent, "go")
        assert world.queue == {}
    else:
        world.agent.start()
        approve(world)
        world.agent.build(background_tasks=True)
        with pytest.raises(OversightError, match="background tasks"):
            world.agent.resume()
    assert world.written == []


def test_all_calls_pause_at_once_and_resume_one_at_a_time(make, offline):
    world = started(make, PROPOSAL, SECOND)
    assert len(world.agent.interrupts) == 2
    assert world.agent.review.resume_input(world.agent.interrupts) is None
    approve(world, SECOND)
    ready = world.agent.review.resume_input(world.agent.interrupts)
    assert len(ready) == 1
    world.agent.resume()
    assert world.written == [SECOND]
    assert len(world.agent.interrupts) == 1
    assert world.agent.review.resume_input(world.agent.interrupts) is None


def test_what_is_passed_back_on_resume_changes_nothing(make, offline):
    world = started(make)
    approve(world)
    refused = answers(world.agent.interrupts)
    refused[0]["interruptResponse"]["response"] = "no"
    call_agent(world.agent.agent, refused)
    assert world.written == [PROPOSAL]


def test_a_rejected_call_is_reported_as_not_done(make, offline):
    world = started(make)
    world.decide(world.real_token(PROPOSAL), "reject")
    world.agent.resume()
    [result] = [
        b["toolResult"]
        for m in world.agent.agent.messages
        for b in m["content"]
        if "toolResult" in b
    ]
    assert (result["status"], result["content"][0]["text"]) == ("error", NOT_APPROVED)
    assert world.written == []


def test_other_pauses_are_left_to_the_host(make, offline):
    world = World(make, StrandsHarness)
    review = ReviewTools({TOOL: world.tool}, run_id=run_id)

    class Pause:
        def __init__(self, name, reason):
            self.name, self.reason, self.id = name, reason, "i"

    others = [
        Pause("mine", {"x": 1}),
        Pause("reviewproof", "text"),
        Pause("reviewproof", {"reviewproof": {"tool": "other", "key": "k"}}),
    ]
    assert review.resume_input(others) is None


def test_an_edit_that_was_not_recorded_writes_nothing(make, offline):
    world = started(make)
    real = world.real_token(PROPOSAL)
    recorded = {**world.queue[real], "classification_rationale": "recorded"}
    world.decide(real, "edit", recorded)
    world.edits[world.agent.key] = {**recorded, "classification_rationale": "something else"}
    with pytest.raises(GuardError):
        world.agent.resume()
    assert world.written == []


def test_the_agents_time_taken_does_not_matter(make, offline):
    world = started(make)
    approve(world)

    def slow(**arguments):
        time.sleep(0.3)
        world.written.append(arguments)
        return "record saved"

    world.tool._write = slow
    world.agent.resume()
    assert world.agent.replies() == ["record saved"]


def test_it_works_with_invoke_async(make, offline):
    world = World(make, StrandsHarness)
    agent = world.agent.build()
    result = asyncio.run(agent.invoke_async("go"))
    approve(world)
    ready = world.agent.review.resume_input(result.interrupts)
    asyncio.run(agent.invoke_async(ready))
    assert world.written == [PROPOSAL]


@pytest.mark.parametrize("value", [None, "", 7])
def test_a_run_needs_an_id(make, offline, value):
    world = World(make, StrandsHarness)
    agent = world.agent.build()
    agent.state.set("run_id", value)
    with pytest.raises(OversightError, match="run ID"):
        call_agent(agent, "go")
    assert world.queue == {}


@pytest.mark.parametrize("tools", [{}, None, {TOOL: print}, {1: None}])
def test_bad_tools(tools):
    with pytest.raises(OversightError, match="ReviewedTool"):
        ReviewTools(tools, run_id=run_id)


def test_bad_settings(make, offline):
    world = World(make, StrandsHarness)
    with pytest.raises(OversightError, match="run_id"):
        ReviewTools({TOOL: world.tool}, run_id="run-1")
    review = ReviewTools({TOOL: world.tool}, run_id=run_id)
    with pytest.raises(OversightError, match="no reviewed tool"):
        review.tool("other", description="o", input_schema=SCHEMA)


# ----------------------------------------------------------------- the guide


def test_the_python_in_the_strands_guide_runs(tmp_path, monkeypatch, offline):
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)
    text = (ROOT / "docs" / "guides" / "strands.md").read_text(encoding="utf-8")
    [snippet] = re.findall(r"^```python\n(.*?)^```$", text, flags=re.MULTILINE | re.DOTALL)
    deviation = {**ITEMS[0], "record_id": "QMS-701"}
    namespace = {"model": ScriptedModel(calls([deviation], name="save_deviation"))}
    exec(compile(snippet, "strands.md", "exec"), namespace)  # noqa: S102
    assert namespace["written"] == ["QMS-701"]
