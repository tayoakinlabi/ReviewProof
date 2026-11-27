# Microsoft Agent Framework

For engineers who want the writes of a Microsoft Agent Framework agent reviewed. Read the
[integration guide](integration.md) first, above all
[what reviewers must not see](integration.md#what-reviewers-must-not-see).

> **Status:** pre-release and not pre-validated. Tested with `agent-framework-core` 1.26.0 and,
> in CI, the newest release. **Needs Python 3.11 or later.**

```bash
pip install "reviewproof[agent-framework]"
```

On Python 3.10, that installs ReviewProof without Agent Framework.

## First: always use a session

**Run the agent with a session, the same one when you resume.** The answer to a pause only
counts in the session that asked. In a test without a session, the library ignored an approval:
nothing was written, and the model carried on as if finished. ReviewProof cannot raise an error
then, because its tool is never called.

## Then: catch `RunStopped`

**When a reviewed tool fails, the adapter raises `RunStopped`, and `except Exception` does not
catch it.** Catch it by name wherever you run the agent. In a web server or job worker, an error
outside `Exception` may get past your usual error handling, so test what yours does.

It is made this way because the library turns a tool's error into a message for the model, which
may then try again. The library has its own signal for aborting a run, `MiddlewareFailure`, but
in a test a host middleware that recovers from errors caught that too.

After `RunStopped`, **do not use that session again.** Its `__cause__` says what happened. The
session's reviewed tools refuse to write any more, but the library would still ask the model.
Check the target system: the write may have happened. Calls answered together run together, so
others may have written although the run returned nothing.

## How it fits together

ReviewProof builds the tools your agent uses for writes. A run pauses with approval requests. You
submit them to review, and store the session and the requests. Once every call has been decided,
you resume.

`model_client` below is your agent's chat client:

```python
import asyncio
import json

from agent_framework import Agent, AgentSession, Content

from reviewproof import Oversight, initialise
from reviewproof.adapters.agent_framework import ReviewTools, RunStopped
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
review = ReviewTools({"save_deviation": tool})
agent = Agent(
    client=model_client,
    tools=[
        review.tool(
            "save_deviation",
            description="Record a deviation.",
            input_schema=arguments_schema,
        )
    ],
)


def keep(response, session):
    """Send what is waiting to review. Returns what to store with the run."""
    requests = response.user_input_requests
    review.submit(requests, session)  # the tickets go to your review queue
    return {"session": session.to_dict(), "requests": [r.to_dict() for r in requests]}


async def start(run_id):
    session = AgentSession(session_id=run_id)  # one ID for each run
    response = await agent.run("Record the deviation.", session=session)
    return keep(response, session)


async def resume(stored):
    session = AgentSession.from_dict(stored["session"])
    requests = [Content.from_dict(r) for r in stored["requests"]]
    message = review.decisions(requests, session)
    if message is None:
        return stored  # nothing waits, or not everything has been decided
    response = await agent.run(message, session=session)
    return keep(response, session)  # replaces what you stored


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
    stored = asyncio.run(resume(stored))
except RunStopped:
    raise  # record it, check the target system, and do not use this session again
assert written == ["QMS-801"]
assert stored["requests"] == []  # nothing is waiting any more
```

## Rules

- **A run resumes only when every request it waits on has been answered.** The library runs
  approved calls only then, so one slow review holds up the others. `decisions()` returns None
  until all reviewed calls are decided. If the run also waits on other requests, add your answers
  to the message's `contents`.
- **Keep only the newest session and requests.** Restoring an older pause runs finished calls
  again. ReviewProof refuses the second write, and the run stops.
- **The session ID names the run.** Give each run its own, and keep it. With the tool-call ID,
  it makes the key for each call.
- **Give the agent only the tools from `review.tool()`** for these writes, never your write
  function. No argument may be called `reviewproof_context`.
- **A rejected call never runs.** The model is told what the library says, which is
  `Error: Tool call invocation was rejected by user.` The wording cannot be changed here.
- **Standing approvals do not get round review.** With the library's approval middleware,
  "always approve" ran a later call with no pause. ReviewProof refused it, because nothing had
  sent it to review, and the run stopped.
- **Add no middleware that replaces a reviewed tool's result.** Middleware can change what the
  model is told, and ReviewProof cannot prevent it.
- **Edits** need `approved_payload` on the `ReviewedTool`: see
  [agents that pause](integration.md#agents-that-pause-and-resume). The library cannot carry an
  edit back: it ignored an approval with changed arguments in a test.

**Not tested yet:** workflows, multi-agent setups and agents used as tools, conversations stored
by the model service, real provider clients, and running tools one at a time
(`allow_concurrent_invocation=False`). Test any you use before relying on them.

## What leaves your computer

ReviewProof sends nothing and changes no settings. The library's OpenTelemetry instrumentation
is on by default, and its source leaves sensitive data out by default. In ReviewProof's tests,
with no exporter set up, no connection was attempted. If you set up an exporter, decide what it
may send, and keep reviewers away from it. `ENABLE_INSTRUMENTATION=false` turns instrumentation
off.

## When to resume

**Resume on a schedule, not straight after each decision,** unless your review queue is busy
enough that nobody can connect the two. When the agent continues, it may send its next action to
review. In a quiet queue, an item that arrives right after a decision shows that the decided item
was real. A schedule alone may not hide this in a very quiet queue: if only one decision was
made since the last resume, what arrives next still points to it. Make sure enough other work
arrives, or that reviewers cannot see when items arrive.
