# OpenAI Agents SDK

For engineers who want the writes of an OpenAI Agents SDK agent reviewed. Read the
[integration guide](integration.md) first, above all
[what reviewers must not see](integration.md#what-reviewers-must-not-see).

> **Status:** pre-release and not pre-validated. Tested with `openai-agents` 0.26.0 and, in CI,
> the newest release.

```bash
pip install "reviewproof[openai-agents]"
```

## Tracing: decide this first

**By default the SDK sends traces to OpenAI,** and its source includes tool inputs and outputs
in them. In ReviewProof's test with default settings, the SDK tried to connect to
`api.openai.com`; with tracing disabled it did not try. If that data must not leave your
systems, disable tracing before anything runs:

```bash
export OPENAI_AGENTS_DISABLE_TRACING=1
```

Or call `agents.set_tracing_disabled(True)`. To keep tracing but leave content out, set
`OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA=false`; data about your runs is still sent.
ReviewProof changes none of these settings. Your model provider receives what the agent sends it
in any case. Planted items never reach the agent, so they are in none of this, but **reviewers
must not be able to read your traces**.

## How it fits together

ReviewProof builds the function tools your agent uses for writes. The SDK pauses at each call.
You submit the calls to review and save the run's state. After decisions, you restore the state,
let ReviewProof approve or reject what has been decided, and run it again.

`model` below is your agent's model:

```python
import asyncio
import json

from agents import Agent, Runner, RunState

from reviewproof import Oversight, initialise
from reviewproof.adapters.openai_agents import ReviewTools
from reviewproof.adapters.tool import ReviewedTool

settings = "examples/deviation_triage/reviewproof.yaml"
with open("examples/deviation_triage/item.schema.json", encoding="utf-8") as file:
    item = json.load(file)
# The tool takes the item's fields, and the ID of the record to write.
arguments_schema = {
    **item,
    "properties": {**item["properties"], "record_id": {"type": "string"}},
    "required": [*item["required"], "record_id"],
}
initialise("oversight.db", "planted.db")  # once, when you set up
review_queue = {}
written = []


def save_deviation(record_id, **fields):  # your write function
    written.append(record_id)
    return "saved"


tool = ReviewedTool(
    lambda: Oversight(settings, "deviation-triage"),
    save_deviation,
    target_argument="record_id",
    enqueue=lambda ticket: review_queue.setdefault(ticket.token, ticket.payload),
)
review = ReviewTools({"save_deviation": tool}, run_id=lambda context: context["run_id"])
agent = Agent(
    name="writer",
    model=model,
    tools=[
        review.function_tool(
            "save_deviation",
            description="Record a deviation.",
            params_json_schema=arguments_schema,
            strict_json_schema=False,  # True if your schema meets OpenAI's strict rules
        )
    ],
)


async def start(run_id):
    result = await Runner.run(agent, "Record the deviation.", context={"run_id": run_id})
    review.submit(result)  # the tickets go to your review queue
    return result.to_state().to_json()  # save this with the run


async def resume(run_id, saved):
    state = await RunState.from_json(agent, saved)
    if not review.apply_decisions(state, run_id):
        return saved  # nothing has been decided
    result = await Runner.run(agent, state)
    review.submit(result)  # calls still waiting, and any new ones
    return result.to_state().to_json()  # replaces the saved state


# 1. Run until it pauses.
saved = asyncio.run(start("run-7"))
assert written == []

# 2. In your review application, after every decision.
with Oversight(settings, "deviation-triage") as oversight:
    for token, payload in review_queue.items():
        oversight.shown(token, reviewer="r-104")
        oversight.decided(token, reviewer="r-104", decision="approve")
        oversight.settle(token, payload)

# 3. Resume.
saved = asyncio.run(resume("run-7", saved))
assert written == ["QMS-501"]
```

## Rules

- **Always save, and resume from, the newest state.** The SDK keeps each finished call's result
  in it. An older state makes the SDK run finished calls again. ReviewProof refuses the second
  write, and the run breaks.
- **Give the agent only the tools from `review.function_tool()`** for these writes, never your
  write function.
- **Do not give those tools a time limit or an output guardrail.** In tests, a time limit told
  the model the call had timed out while the write went on, and an output guardrail told it a
  finished write was blocked. Either way the run continued and the model could try again.
  `submit()` refuses both, and the tool checks again before it writes.
- **Never use `always_approve`.** The SDK then runs later calls with no pause. ReviewProof
  refuses them, because nothing sent them to review.
- **Resume with `apply_decisions()`.** It approves or rejects only what has been decided. The
  recorded decision is what counts: approving a call yourself does not let it write.
- **The run ID** comes from the context you pass to `Runner.run`, and must be the same after the
  state is saved and restored. A plain dictionary does that. With the SDK's tool-call ID, it
  makes the key for each call.
- **Let errors reach your application.** The SDK raises `UserError`, with ReviewProof's error or
  your write function's as its cause. **Do not carry on from a saved state after a failed
  write:** check the target system. Calls approved together run together, so others may have
  written even though the run returned nothing.
- **Calls to your other tools are left to you,** both to review and to approve.
- **Edits** need `approved_payload` on the `ReviewedTool`: see
  [agents that pause](integration.md#agents-that-pause-and-resume).

**Not tested yet:** streamed runs, handoffs, agents used as tools, SDK sessions, and tools in
strict schema mode. Test any you use before relying on them.

## When to resume

**Resume on a schedule, not straight after each decision,** unless your review queue is busy
enough that nobody can connect the two. When the agent continues, it may send its next action to
review. In a quiet queue, an item that arrives right after a decision shows that the decided item
was real. A schedule alone may not hide this in a very quiet queue: if only one decision was
made since the last resume, what arrives next still points to it. Make sure enough other work
arrives, or that reviewers cannot see when items arrive.
