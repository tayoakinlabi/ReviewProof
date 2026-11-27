# Strands Agents

For engineers who want the writes of a Strands agent reviewed. Read the
[integration guide](integration.md) first, above all
[what reviewers must not see](integration.md#what-reviewers-must-not-see).

> **Status:** pre-release and not pre-validated. Tested with `strands-agents` 1.67.0 and, in
> CI, the newest release.

```bash
pip install "reviewproof[strands]"
```

## First: pass `hooks=[review]`, and no background tasks

`ReviewTools` builds your reviewed tools **and** is a Strands hook provider. Give the agent
both: `Agent(tools=[review.tool(...)], hooks=[review])`.

The hooks do two jobs: they pause each reviewed call, and they raise `RunStopped` after a
reviewed tool fails. **Without them the tools refuse to write, but nothing can stop the run,**
so the model is told that the call did not complete and carries on.

**Do not turn on background tasks** on this agent. In a test, Strands answered the model before
a background write had finished. The tools refuse to run on such an agent.

## After a failure

By default Strands turns a tool's error into a message for the model, which may then try again.
So a reviewed tool raises nothing. It marks the agent as failed, and the hooks raise
`RunStopped` before the model is asked again, or when the invocation ends without asking it, for
example because another call is still waiting for review. The model's history says only that
the action did not complete, not why. `RunStopped.__cause__` says why.

**Do not use that agent or its session again.** A failed agent stays failed, and a saved session
normally remembers it. One case does not: when the run was about to pause for another call,
Strands' saved session can be the one from the earlier pause. Resuming it runs the failed call
again, which ReviewProof refuses. So keep your own record of stopped runs.

Check the target system: the write may have happened, and so may others that were running at
the same time.

## How it fits together

`model` below is your agent's model:

```python
import json

from strands import Agent

from reviewproof import Oversight, initialise
from reviewproof.adapters.strands import ReviewTools, RunStopped
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
review = ReviewTools({"save_deviation": tool}, run_id=lambda agent: agent.state.get("run_id"))
agent = Agent(
    model=model,
    tools=[
        review.tool(
            "save_deviation",
            description="Record a deviation.",
            input_schema=arguments_schema,
        )
    ],
    hooks=[review],  # required
    state={"run_id": "run-7"},  # saved with the session, if you use a session manager
    callback_handler=None,
)

# 1. Run until it pauses. The tickets are now in your review queue.
result = agent("Record the deviation.")
assert result.stop_reason == "interrupt"
waiting = result.interrupts  # keep these with the run
assert written == []

# 2. In your review application, after every decision.
with Oversight(settings, "deviation-triage") as oversight:
    for token, payload in review_queue.items():
        oversight.shown(token, reviewer="r-104")
        oversight.decided(token, reviewer="r-104", decision="approve")
        oversight.settle(token, payload)

# 3. Resume whatever has been decided. None means nothing is ready.
ready = review.resume_input(waiting)
if ready is not None:
    try:
        result = agent(ready)
    except RunStopped:
        raise  # record it, check the target system, and do not use this agent again
    waiting = result.interrupts or []  # replaces what you kept
assert written == ["QMS-701"]
assert not waiting
```

## Rules

- **Calls pause and resume one at a time.** `resume_input()` answers only the pauses whose
  action has been decided. What it passes back is ignored: the recorded decision is what counts.
  A pause that has been resumed is no longer waiting, so **keep only the latest result's
  interrupts.**
- **Give the agent only the tools from `review.tool()`** for these writes, never your write
  function. If another tool has a reviewed tool's name, the call is stopped before it runs.
- **The run ID** comes from your `run_id(agent)` function and must be the same after a saved
  session is restored. `agent.state` is saved with the session. With the tool-call ID, it makes
  the key for each call.
- **Hooks and middleware of your own** can change what the model is told about a reviewed call.
  A retry hook cannot write twice: ReviewProof refuses, and the run stops. Do not add hooks
  that rewrite a reviewed tool's result.
- **The pause names the tool and the key,** and holds no ticket or content. Do not build your
  review screen from it.
- **Calls to your other tools are left to you,** and so are your own pauses.
- **Edits** need `approved_payload` on the `ReviewedTool`: see
  [agents that pause](integration.md#agents-that-pause-and-resume).

**Not tested yet:** multi-agent graphs and swarms, agents used as tools, bidirectional
(streaming) agents, checkpointing, middleware, interventions, and session managers other than
the file one. Test any you use before relying on them.

## What leaves your computer

ReviewProof sends nothing and changes no settings. In ReviewProof's tests of Strands, with
telemetry not set up, no connection was attempted. Strands' default model provider is a cloud
service, so check which model your agent uses. If you set up tracing, decide what it may send,
and keep reviewers away from it.

## When to resume

**Resume on a schedule, not straight after each decision,** unless your review queue is busy
enough that nobody can connect the two. When the agent continues, it may send its next action to
review. In a quiet queue, an item that arrives right after a decision shows that the decided item
was real. A schedule alone may not hide this in a very quiet queue: if only one decision was
made since the last resume, what arrives next still points to it. Make sure enough other work
arrives, or that reviewers cannot see when items arrive.
