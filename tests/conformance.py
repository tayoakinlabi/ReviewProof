"""The behaviour every adapter for an agent library must have (ADR-005).

To test an adapter, write a ``Harness`` that drives the real library with a scripted model, and
a test class that sets ``harness`` to it::

    class TestLibraryAdapter(AdapterConformance):
        harness = LibraryHarness

No network and no API key may be needed. Every test blocks connections that leave the computer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import pytest
from deploy import ITEMS, QUEUE

from reviewproof import CallState, GuardError, Oversight, OversightError
from reviewproof.adapters.tool import NOT_APPROVED, ReviewedTool, ReviewPending

PROPOSAL = {"record_id": "QMS-REC-0001", **ITEMS[0], "deviation_id": "DEV-2025-7001"}
SECOND = {"record_id": "QMS-REC-0002", **ITEMS[1], "deviation_id": "DEV-2025-7002"}


class Harness(ABC):
    """One agent whose model proposes calls to the reviewed tool, all in its first message,
    one for each of ``proposals``. Where a method speaks of "the call", it means the first.
    """

    def __init__(self, tool: ReviewedTool, *proposals: dict[str, Any]) -> None:
        self.tool = tool
        self.proposals = proposals
        self.arguments = proposals[0]

    @abstractmethod
    def start(self) -> None:
        """Run the agent until it pauses for review."""

    @abstractmethod
    def saved_state(self) -> str:
        """Everything the library keeps or hands the host at the pause, as text."""

    @abstractmethod
    def resume(self) -> None:
        """Resume, as the host should, every call whose action has been decided."""

    @abstractmethod
    def force_resume(self) -> None:
        """Tell the library the call is approved, whatever ReviewProof says."""

    @abstractmethod
    def run_without_pause(self) -> None:
        """Let the library run the tool with no pause at all, as a standing approval would."""

    @abstractmethod
    def snapshot(self) -> Any:
        """The saved pause, to restore later."""

    @abstractmethod
    def restore(self, snapshot: Any) -> None:
        """Put the agent back at a saved pause, as a second process or a replay would."""

    @property
    @abstractmethod
    def key(self) -> str:
        """The key the adapter uses for the proposed call."""

    @abstractmethod
    def replies(self) -> list[str]:
        """The tool results the model has been given."""

    @abstractmethod
    def model_calls(self) -> int:
        """How many times the model has been asked."""


class World:
    """A deployment, a review queue, a target system and one agent."""

    def __init__(
        self, make: Callable, harness: type[Harness], proposals: tuple = (PROPOSAL,)
    ) -> None:
        self.deployment = make(rate=0.5)  # a planted item with every submission
        self.reviews = self.deployment.open()
        self.deployment.load_planted(self.reviews)
        self.queue: dict[str, Any] = {}
        self.written: list[dict[str, Any]] = []
        self.failure: Exception | None = None
        self.edits: dict[str, dict[str, Any]] = {}
        self.tool = self.make_tool(approved_payload=self.edit_for)
        self.agent = harness(self.tool, *(dict(p) for p in proposals))
        self._harness = harness

    def make_tool(self, **options: Any) -> ReviewedTool:
        deployment = self.deployment
        return ReviewedTool(
            lambda: Oversight(deployment.config, QUEUE, deployment.db, deployment.planted_db),
            self.write,
            target_argument="record_id",
            enqueue=lambda ticket: self.queue.setdefault(ticket.token, ticket.payload),
            **options,
        )

    def write(self, **arguments: Any) -> str:
        assert not arguments["record_id"].startswith("rp-reserved-")
        self.written.append(arguments)
        if self.failure:
            raise self.failure
        return "record saved"

    def edit_for(self, key: str, proposal: dict[str, Any]) -> dict[str, Any]:
        return self.edits.get(key, proposal)

    # ---- the review screen: it sees tickets and tokens only

    def tickets(self) -> tuple[str, str]:
        """(real, planted) tokens. Only the test may know which is which."""
        assert len(self.queue) == 2
        proposed = {k: v for k, v in PROPOSAL.items() if k != "record_id"}
        real = next(t for t, payload in self.queue.items() if payload == proposed)
        return real, next(t for t in self.queue if t != real)

    def real_token(self, proposal: dict[str, Any]) -> str:
        content = {k: v for k, v in proposal.items() if k != "record_id"}
        return next(t for t, payload in self.queue.items() if payload == content)

    def decide(self, token: str, decision: str = "approve", final: Any = None) -> CallState:
        self.reviews.shown(token, "r-1")
        self.reviews.decided(token, "r-1", decision, final_payload=final)
        status = self.reviews.settle(token, self.queue[token] if final is None else final)
        return status.state

    def events(self) -> int:
        return self.deployment.query("SELECT COUNT(*) AS n FROM events")[0]["n"]


class AdapterConformance:
    harness: type[Harness]

    @pytest.fixture
    def world(self, make, offline):
        return World(make, self.harness)

    def test_nothing_is_written_before_review(self, world):
        world.agent.start()
        assert world.written == []
        assert len(world.queue) == 2

    def test_nothing_planted_and_no_token_is_in_the_agents_state(self, world):
        world.agent.start()
        state = world.agent.saved_state()
        real, planted = world.tickets()
        assert PROPOSAL["deviation_id"] in state  # the search does see the content
        assert world.queue[planted]["deviation_id"] not in state
        assert "rp-reserved-" not in state
        assert real not in state and planted not in state

    def test_an_approved_call_runs_once_with_exactly_what_was_reviewed(self, world):
        world.agent.start()
        real, planted = world.tickets()
        assert world.decide(planted) is CallState.WAITING
        assert world.decide(real) is CallState.APPROVE
        world.agent.resume()
        assert world.written == [PROPOSAL]
        assert any("record saved" in reply for reply in world.agent.replies())
        assert world.deployment.verify().ok

    def test_approving_the_planted_item_wakes_nothing(self, world):
        world.agent.start()
        _, planted = world.tickets()
        world.decide(planted)
        assert world.tool.state(world.agent.key) is CallState.WAITING
        calls = world.agent.model_calls()
        with pytest.raises(ReviewPending):
            world.agent.force_resume()
        assert world.written == []
        assert world.agent.model_calls() == calls

    def test_a_forged_resume_writes_nothing(self, world):
        world.agent.start()
        with pytest.raises(ReviewPending):
            world.agent.force_resume()
        assert world.written == []

    def test_running_the_tool_without_a_pause_writes_nothing(self, world):
        with pytest.raises(OversightError, match="nothing in this queue"):
            world.agent.run_without_pause()
        assert world.written == []
        assert world.queue == {}

    def test_a_rejected_call_writes_nothing_and_the_agent_is_told(self, world):
        world.agent.start()
        real, planted = world.tickets()
        assert world.decide(real, "reject") is CallState.REJECT
        world.decide(planted, "reject")
        world.agent.resume()
        assert world.written == []
        assert any(NOT_APPROVED in reply for reply in world.agent.replies())

    def test_an_edited_call_writes_the_edit_and_says_so(self, world):
        world.agent.start()
        real, _ = world.tickets()
        edited = {**world.queue[real], "classification_rationale": "Changed by the reviewer."}
        world.edits[world.agent.key] = edited
        assert world.decide(real, "edit", edited) is CallState.EDIT
        world.agent.resume()
        assert world.written == [{**edited, "record_id": PROPOSAL["record_id"]}]
        [reply] = world.agent.replies()
        assert "changes a reviewer made to: classification_rationale." in reply

    def test_an_edit_the_adapter_cannot_get_writes_nothing(self, world):
        world.tool = world.agent.tool = world.make_tool()  # no approved_payload
        world.agent.start()
        real, _ = world.tickets()
        edited = {**world.queue[real], "classification_rationale": "Changed by the reviewer."}
        world.decide(real, "edit", edited)
        with pytest.raises(GuardError, match="not the one that was approved"):
            world.agent.resume()
        assert world.written == []
        assert world.agent.replies() == []

    def test_a_planted_decision_sends_the_agent_nothing(self, world):
        world.agent.start()
        _, planted = world.tickets()
        calls = world.agent.model_calls()
        world.decide(planted, "reject")
        assert world.agent.model_calls() == calls
        assert world.agent.replies() == []

    def test_restoring_a_pause_and_resuming_again_writes_once(self, world):
        world.agent.start()
        real, _ = world.tickets()
        world.decide(real)
        saved = world.agent.snapshot()
        world.agent.resume()
        world.agent.restore(saved)
        with pytest.raises(OversightError, match="already been requested"):
            world.agent.force_resume()
        assert len(world.written) == 1

    def test_running_the_pause_again_creates_nothing_new(self, world):
        world.agent.start()
        tickets, events = set(world.queue), world.events()
        saved = world.agent.snapshot()
        world.agent.restore(saved)
        with pytest.raises(ReviewPending):
            world.agent.force_resume()
        assert set(world.queue) == tickets
        assert world.events() == events

    def test_two_calls_proposed_at_once_each_run_once(self, make, offline):
        """A library that runs a step again must not reach an earlier call's write again."""
        world = World(make, self.harness, (PROPOSAL, SECOND))
        world.agent.start()
        world.decide(world.real_token(SECOND))
        world.agent.resume()
        assert world.written == [SECOND]
        world.decide(world.real_token(PROPOSAL))
        world.agent.resume()
        assert world.written == [SECOND, PROPOSAL]
        assert len(world.agent.replies()) == 2
        assert world.deployment.verify().ok

    def test_two_calls_decided_before_one_resume_each_run_once(self, make, offline):
        world = World(make, self.harness, (PROPOSAL, SECOND))
        world.agent.start()
        world.decide(world.real_token(PROPOSAL))
        world.decide(world.real_token(SECOND), "reject")
        world.agent.resume()
        assert world.written == [PROPOSAL]
        replies = world.agent.replies()
        assert len(replies) == 2 and any(NOT_APPROVED in reply for reply in replies)

    @pytest.mark.parametrize("error", [ConnectionError("down"), ValueError("bad record")])
    def test_a_failed_write_stops_the_run(self, world, error):
        world.agent.start()
        real, _ = world.tickets()
        world.decide(real)
        world.failure = error
        calls = world.agent.model_calls()
        with pytest.raises(Exception) as raised:
            world.agent.resume()
        chain, current = [], raised.value
        while current is not None:
            chain.append(current)
            current = current.__cause__ or current.__context__
        assert error in chain
        assert world.agent.model_calls() == calls, "the model was given a chance to try again"
        assert world.agent.replies() == []
        assert len(world.written) == 1
