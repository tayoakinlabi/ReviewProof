# Pydantic AI

For engineers who want the writes of a Pydantic AI agent reviewed. Read the
[integration guide](integration.md) first, above all
[what reviewers must not see](integration.md#what-reviewers-must-not-see).

> **Status:** pre-release and not pre-validated. Tested with `pydantic-ai-slim` 2.76.0 and, in
> CI, the newest release. **Needs Python 3.11 or later.**

```bash
pip install "reviewproof[pydantic-ai]"
```

On Python 3.10, that installs ReviewProof without Pydantic AI.

## First: catch `RunStopped`

**When a reviewed tool fails, the adapter raises `RunStopped`, and `except Exception` does not
catch it.** Catch it by name wherever you run the agent. In a web server or job worker, an error
outside `Exception` may get past your usual error handling, so test what yours does.

It is made this way because Pydantic AI turns some errors into a message for the model, which
then tries again. In tests, that happened with a Pydantic `ValidationError` raised inside a tool
(your write function could raise one while checking your target system's reply, after writing),
with the library's own `ModelRetry` and `ToolFailed`, and with a time limit, while the write
went on. A hook that recovers from errors did the same to an ordinary error.

After `RunStopped`, **do not resume the run.** Its `__cause__` says what happened. Check the
target system: the write may have happened. Calls approved together run together, so others may
have written although the run returned nothing.

## How it fits together

ReviewProof builds the tools your agent uses for writes. A run pauses with a
`DeferredToolRequests`. You submit its calls to review, and store the messages and the pause.
Once every call has been decided, you resume.

`model` below is your agent's model:

```python
import asyncio
import json

from pydantic import TypeAdapter
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelMessagesTypeAdapter

from reviewproof import Oversight, initialise
from reviewproof.adapters.pydantic_ai import ReviewTools, RunStopped
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
review = ReviewTools({"save_deviation": tool}, run_id=lambda deps: deps["run_id"])
agent = Agent(
    model,
    tools=[
        review.tool(
            "save_deviation",
            description="Record a deviation.",
            parameters_json_schema=arguments_schema,
        )
    ],
    output_type=[str, DeferredToolRequests],
)
PAUSE = TypeAdapter(DeferredToolRequests)


def keep(result, run_id):
    """Send what is waiting to review. Returns what to store with the run."""
    pause = None
    if isinstance(result.output, DeferredToolRequests):
        review.submit(result.output, run_id)  # the tickets go to your review queue
        pause = PAUSE.dump_json(result.output)
    return result.all_messages_json(), pause


async def start(run_id):
    result = await agent.run("Record the deviation.", deps={"run_id": run_id})
    return keep(result, run_id)


async def resume(run_id, stored):
    messages, pause = stored
    if pause is None:
        return stored
    answers = review.decisions(PAUSE.validate_json(pause), run_id)
    if answers is None:
        return stored  # not everything has been decided
    result = await agent.run(
        message_history=ModelMessagesTypeAdapter.validate_json(messages),
        deferred_tool_results=answers,
        deps={"run_id": run_id},
    )
    return keep(result, run_id)  # replaces what you stored


# 1. Run until it pauses.
stored = asyncio.run(start("run-7"))
assert written == []

# 2. In your review application, after every decision.
with Oversight(settings, "deviation-triage") as oversight:
    for token, payload in review_queue.items():
        oversight.shown(token, reviewer="r-104")
        oversight.decided(token, reviewer="r-104", decision="approve")
        oversight.settle(token, payload)

# 3. Resume.
try:
    stored = asyncio.run(resume("run-7", stored))
except RunStopped:
    raise  # record it, check the target system, and do not resume this run
assert written == ["QMS-601"]
assert stored[1] is None  # nothing is waiting any more
```

## Rules

- **A run resumes only when every call it waits on has been decided.** Pydantic AI takes all the
  answers together, so one slow review holds up the others. `decisions()` returns None until
  then.
- **Keep only the newest messages and pause.** Resuming an older pause runs finished calls
  again. ReviewProof refuses the second write, and the run stops.
- **Give the agent only the tools from `review.tool()`** for these writes, never your write
  function. Do not rename or wrap them in a toolset that changes their names: `submit()` finds
  its calls by name.
- **No time limit.** A limit on the whole agent does not apply to these tools. If one is put on
  the tool itself, it refuses to write.
- **Add no hook or capability that acts on what a reviewed tool returns.** In a test, a hook that
  raised `ModelRetry` after the tool succeeded told the model to try again, although the write
  had happened. ReviewProof cannot prevent this.
- **The run ID** comes from the dependencies you pass to `agent.run`. Give the same one to
  `submit()` and `decisions()`. With the tool-call ID, it makes the key for each call.
- **Calls to your other tools are left to you.** If the run also waits on some, add your answers
  to the result of `decisions()` before you resume. If it waits on none of ReviewProof's,
  `decisions()` returns None.
- **Edits** need `approved_payload` on the `ReviewedTool`: see
  [agents that pause](integration.md#agents-that-pause-and-resume).

**Not tested yet:** streamed runs, durable execution frameworks, agents that delegate to other
agents, and toolsets that wrap or rename tools. Test any you use before relying on them.

## What leaves your computer

ReviewProof sends nothing and changes no settings. In ReviewProof's tests of Pydantic AI, with
instrumentation not set up, no connection was attempted. If you set up instrumentation, such as
Logfire or OpenTelemetry, decide what it may send, and keep reviewers away from it. The library
prints a notice about this; `PYDANTIC_AI_NO_BANNER=1` turns the notice off.

## When to resume

**Resume on a schedule, not straight after each decision,** unless your review queue is busy
enough that nobody can connect the two. When the agent continues, it may send its next action to
review. In a quiet queue, an item that arrives right after a decision shows that the decided item
was real. A schedule alone may not hide this in a very quiet queue: if only one decision was
made since the last resume, what arrives next still points to it. Make sure enough other work
arrives, or that reviewers cannot see when items arrive.
