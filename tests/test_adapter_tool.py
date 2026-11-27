"""The shared part of the agent-library adapters, run against a stand-in library.

The stand-in behaves as badly as the real ones were observed to (ADR-005): a paused step runs
again from its start, a saved pause can be resumed any number of times, resuming is believed
without question, and the tool can be run with no pause.
"""

import copy
import json

import pytest
from conformance import PROPOSAL, AdapterConformance, Harness, World

from reviewproof import GuardError, OversightError
from reviewproof.adapters.tool import NOT_APPROVED, ReviewedTool, ReviewPending


class _Pause(Exception):
    pass


class StandInHarness(Harness):
    key = "thread-1/call-1"

    def __init__(self, tool, arguments):
        super().__init__(tool, arguments)
        self.state = {"call": {"id": "call-1", "arguments": arguments}, "approved": False}
        self.state["replies"] = []
        self.asked = 0

    def _ask_model(self):
        self.asked += 1

    def _step(self):
        """The paused step. Every resume runs it from the top."""
        call = self.state["call"]
        self.tool.submit(self.key, call["arguments"])
        if not self.state["approved"]:
            raise _Pause("A review is pending.")
        outcome = self.tool.run(self.key, call["arguments"])
        self.state["replies"].append(json.dumps(outcome.reply))
        self._ask_model()

    def start(self):
        self._ask_model()
        with pytest.raises(_Pause):
            self._step()

    def saved_state(self):
        return json.dumps(self.state) + "A review is pending."

    def resume(self):
        self.force_resume()

    def force_resume(self):
        self.state["approved"] = True
        self._step()

    def run_without_pause(self):
        self.tool.run(self.key, self.arguments)

    def snapshot(self):
        return copy.deepcopy(self.state)

    def restore(self, snapshot):
        self.state = copy.deepcopy(snapshot)

    def replies(self):
        return list(self.state["replies"])

    def model_calls(self):
        return self.asked


class TestStandInAdapter(AdapterConformance):
    harness = StandInHarness


# ----------------------------------------------------------------- the shared part alone


@pytest.fixture
def world(make, offline):
    return World(make, StandInHarness)


def approved(world):
    world.tool.submit("k", PROPOSAL)
    real, _ = world.tickets()
    world.decide(real)
    return real


@pytest.mark.parametrize(
    "change",
    [
        {"record_id": "QMS-REC-0002"},
        {"classification_rationale": "something nobody reviewed"},
        {"extra_argument": 1},
    ],
)
def test_arguments_changed_on_resume_write_nothing(world, change):
    approved(world)
    events = world.events()
    with pytest.raises(OversightError, match="already used"):
        world.tool.run("k", {**PROPOSAL, **change})
    assert world.written == []
    assert world.events() == events
    assert world.tool.run("k", PROPOSAL).executed


def test_submit_and_run_return_no_ticket(world):
    assert world.tool.submit("k", PROPOSAL) is None
    real, planted = world.tickets()
    world.decide(real)
    outcome = world.tool.run("k", PROPOSAL)
    assert (outcome.executed, outcome.reply, outcome.edited_fields) == (True, "record saved", ())
    assert "record saved" not in repr(outcome)
    assert real not in str(outcome) and planted not in str(outcome)


def test_a_rejection_is_a_reply_and_may_be_asked_for_again(world):
    world.tool.submit("k", PROPOSAL)
    real, _ = world.tickets()
    world.decide(real, "reject")
    for _ in range(2):
        outcome = world.tool.run("k", PROPOSAL)
        assert (outcome.executed, outcome.reply) == (False, NOT_APPROVED)
    assert world.written == []


def test_every_submit_hands_over_every_ticket_so_a_crash_loses_nothing(world):
    handed = []
    deployment = world.deployment
    fail = [True]

    def enqueue(ticket):
        handed.append(ticket.token)
        if fail[0]:
            raise ConnectionError("the queue is down")

    tool = ReviewedTool(world.tool._open, world.write, target_argument="record_id", enqueue=enqueue)
    with pytest.raises(ConnectionError):
        tool.submit("k", PROPOSAL)
    fail[0] = False
    tool.submit("k", PROPOSAL)
    assert len(set(handed)) == 2 and handed[1:].count(handed[0]) == 1
    assert deployment.verify().ok


def test_the_reviewer_is_passed_on_and_must_not_change(world):
    names = iter(["r-1", "r-1", "r-2"])
    tool = world.make_tool(reviewer=lambda key: next(names))
    tool.submit("k", PROPOSAL)
    rows = world.deployment.query("SELECT DISTINCT reviewer FROM submission_keys", planted=True)
    assert [r["reviewer"] for r in rows] == ["r-1"]
    tool.submit("k", PROPOSAL)
    with pytest.raises(OversightError, match="already used"):
        tool.submit("k", PROPOSAL)


@pytest.mark.parametrize(
    ("final", "message"),
    [
        ({"record_id": "QMS-REC-0009"}, "cannot change what is written to"),
        ("not a mapping", "must be a mapping"),
    ],
)
def test_a_bad_edit_writes_nothing(world, final, message):
    world.tool.submit("k", PROPOSAL)
    real, _ = world.tickets()
    world.decide(real, "edit", {**world.queue[real], "classification_rationale": "x"})
    world.edits["k"] = final
    with pytest.raises(OversightError, match=message):
        world.tool.run("k", PROPOSAL)
    assert world.written == []


def test_an_edit_other_than_the_recorded_one_writes_nothing(world):
    world.tool.submit("k", PROPOSAL)
    real, _ = world.tickets()
    recorded = {**world.queue[real], "classification_rationale": "x"}
    world.decide(real, "edit", recorded)
    world.edits["k"] = {**recorded, "classification_rationale": "y"}
    with pytest.raises(GuardError):
        world.tool.run("k", PROPOSAL)
    world.edits["k"] = recorded
    assert world.tool.run("k", PROPOSAL).edited_fields == ("classification_rationale",)


def test_running_before_a_decision(world):
    world.tool.submit("k", PROPOSAL)
    with pytest.raises(ReviewPending, match="Nothing was executed"):
        world.tool.run("k", PROPOSAL)


@pytest.mark.parametrize(
    "arguments",
    [None, {}, {"record_id": 7}, {"record_id": ""}, {"record_id": "QMS-1", "x": object()}],
)
def test_arguments_that_cannot_be_reviewed(world, arguments):
    events = world.events()
    with pytest.raises(OversightError):
        world.tool.submit("k", arguments)
    assert world.events() == events and world.queue == {}


async def _asynchronous(**arguments):
    return None


class _AsynchronousObject:
    async def __call__(self, **arguments):
        return None


@pytest.mark.parametrize("write", [_asynchronous, _AsynchronousObject()])
def test_an_asynchronous_write_is_refused(world, write):
    with pytest.raises(OversightError, match="synchronous"):
        ReviewedTool(world.tool._open, write, target_argument="record_id", enqueue=print)


def test_a_write_that_returns_something_to_await_counts_as_failed(world):
    tool = ReviewedTool(
        world.tool._open,
        lambda **arguments: _asynchronous(),
        target_argument="record_id",
        enqueue=lambda ticket: world.queue.setdefault(ticket.token, ticket.payload),
    )
    tool.submit("k", PROPOSAL)
    real, _ = world.tickets()
    world.decide(real)
    with pytest.raises(TypeError, match="may not have run"):
        tool.run("k", PROPOSAL)
    types = [r["type"] for r in world.deployment.query("SELECT type FROM events")]
    assert "execute_failed" in types and "executed" not in types


@pytest.mark.parametrize("broken", ["open_oversight", "write", "enqueue", "target_argument"])
def test_bad_settings(world, broken):
    settings = {
        "open_oversight": world.tool._open,
        "write": world.write,
        "target_argument": "record_id",
        "enqueue": print,
    }
    settings[broken] = None
    with pytest.raises(OversightError, match=broken):
        ReviewedTool(settings.pop("open_oversight"), settings.pop("write"), **settings)
