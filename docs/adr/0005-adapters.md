# ADR-005: Adapters for agent libraries

- **Status:** Accepted
- **Date:** 2026-11-26
- **Amended:** 2026-11-27 ([LangGraph, and what the review queue can show](#amendment-of-2026-11-27);
  [OpenAI Agents SDK](#amendment-openai-agents-sdk); [Pydantic AI](#amendment-pydantic-ai);
  [Strands Agents](#amendment-strands-agents);
  [Microsoft Agent Framework](#amendment-microsoft-agent-framework))
- **Amends:** ADR-003 (replay-safe `submit`, three new methods, and what server
  code can learn about a pair)

## Context

Five libraries are to get adapters: LangGraph, OpenAI Agents SDK, Microsoft Agent Framework,
Pydantic AI and Strands Agents. Each can pause an agent before a tool runs and resume it later.
An adapter has to fit ReviewProof into that pause without weakening ADR-003: a planted item
never executes, never reaches the agent, and looks like any other item to the reviewer.

## What the libraries do

Observed on 2026-11-26 with a small script for each library, on Python 3.12 and Windows, with
scripted stand-in models, no real API keys, and Python's outside connections blocked (native
code that opens its own connections would not be caught). Versions:
`langgraph` 1.2.24, `openai-agents` 0.26.0, `pydantic-ai-slim` 2.76.0, `strands-agents`
1.67.0, `agent-framework-core` 1.26.0. One script run does not cover every feature. Each
adapter's tests must repeat the observations it relies on.

| Library | Observed |
| --- | --- |
| LangGraph | A paused node runs again from its start. After a node failed, running again replayed the same answers and reached the write again. Resuming from an earlier checkpoint ran the write again. The task namespace stayed the same each time. |
| OpenAI Agents SDK | Restoring one saved pause twice, and approving both, ran the tool twice. After `approve(..., always_approve=True)`, a **later call ran with no pause**. By default a tool error became text for the model; with `failure_error_function=None` the run raised. Its `approve()` takes no edited arguments. With default settings it tried to connect to `api.openai.com`; with `OPENAI_AGENTS_DISABLE_TRACING=1` it made no attempt. Its source includes tool inputs and outputs in traces by default. |
| Pydantic AI | Resuming one saved history twice ran the tool twice. A tool error stopped the run. Edited arguments reached the tool. |
| Strands Agents | On resume, the approval hook ran again from its start. A tool error became text for the model. |
| Agent Framework | Sending one approval twice in a session ran the tool once. An approval with **edited arguments was ignored**: nothing ran, only a log line said so, and the agent still replied. A tool error became text for the model. |

Apart from the OpenAI tracing attempt, no script run tried to connect outside the computer.

So a library's approval cannot be what permits a write, code placed before a pause may run more
than once, and not every library can carry a reviewer's edit back to the tool.

## Decision

### 1. The library's pause is a signal; the guard decides

The adapter wraps the host's write function. The wrapper runs it only through ReviewProof's
guard, which requires a logged `approve` or `edit` for exactly this call and this content, and
runs it at most once. What the library says on resume is not trusted. A forged resume, a
standing approval, a second resume or a replayed checkpoint cannot write anything that was not
reviewed, or write it twice.

This protects only writes made through the wrapper. The host must give the agent the wrapped
tool and never the raw write function.

### 2. `submit` is safe to repeat

`submit(item, reviewer=None, key=None)`. A `key` names one proposed action.

- The first call with a key behaves as today.
- A later call with the same key, queue, target ID, payload and reviewer returns the **same
  tickets**, writes nothing to the log and does not ask the gate again.
- The same key with anything different is refused. This includes a node that builds a new
  proposal each time it runs: build the proposal before the node that pauses.
- If the first call did not reach the log, the next one starts afresh.
- Two calls at once are put in order by the log's write lock.

Tickets are stored only as hashes today. For a keyed submission, each token is derived with
HMAC from a random secret and the item ID. The secret, the key and the item IDs are kept in
`planted.db`, which holds no item content. Someone who can read `planted.db` could rebuild these
tokens; access to it is already restricted. The key is not written to the log, so the log does
not group the two items of a pair. The new table goes into database schema version 2, which has
not been released.

The adapter hands every ticket to the host's `enqueue` function on **every** call, so that a
crash between submitting and queuing loses nothing. `enqueue` must ignore a token it already
has.

**Keys.** The host's run, thread or session ID plus the library's tool-call ID. For a LangGraph
node that is not a tool call: the thread ID, the task namespace and a step name. A model that
repeats a tool-call ID within one run gets a refusal, not an unreviewed write.

### 3. Three methods connect a decision to the paused call

The reviewer's screen sends decisions by token and cannot know which token is the real one. A
planted decision must not wake the agent.

- `settle(token, payload)`: the host calls it after **every** decision, the same way for both
  tickets of a pair, with the payload as decided. For an approval or edit, it checks the payload
  against the decision, for both kinds alike. For a planted item it then does what `execute()`
  does today: the guard records the request, blocks it and withdraws the item. It runs no
  function. Calling it again is harmless. It returns the **status of the real call** under that
  token's key: `waiting`, `approve`, `edit` or `reject`.
- `call_status(key)`: the same status, read only.
- `execute_call(key, payload, executor)`: `execute()` for the real item under a key, with every
  guard check.

When the status is no longer `waiting`, the host resumes the agent using the adapter's helper.
The paused call asks ReviewProof for the status itself. If the status is `waiting`, it stops
with an error.

**What server code can learn.** By watching `settle`, the host's server code can work out which
ticket was real. It already can, because `execute()` returns `executed=False` for a planted
item. The rule stays: nothing of this reaches a reviewer.

### 4. Planted tickets never touch the agent

*What the pause says was [amended](#amendment-of-2026-11-27).*

Tickets go only to `enqueue`. The pause, saved state, checkpoints, messages and anything the
adapter returns to the library hold no ticket token and no planted content. The pause says only
that a review is pending. Each adapter has a test that saves the library's state after a pause
and searches it.

The review screen is built **only from tickets**, never from the library's approval request.
Approval inside the agent's own chat is not supported.

### 5. A failure after approval stops the run

*Further conditions: [OpenAI Agents SDK](#amendment-openai-agents-sdk),
[Pydantic AI](#amendment-pydantic-ai), [Strands Agents](#amendment-strands-agents) and
[Microsoft Agent Framework](#amendment-microsoft-agent-framework).*

If the guard refuses, or the write fails, or it ran but could not be logged, the error reaches
the host. It must not become text that lets the model try again: a retry would be a new call,
and if approved, could repeat a write that had partly happened.

A second attempt at a call that was already tried is refused. ReviewProof does not store the
first result, so the host has to check the target system and the log.

Stopping the run is confirmed for Pydantic AI, for the OpenAI Agents SDK with
`failure_error_function=None`, and for a plain LangGraph node. It is **not yet established**
for Strands, Agent Framework or LangGraph's prebuilt tool node. **An adapter that cannot do
this is not shipped** until a different rule has been agreed and recorded here.

### 6. Tool arguments

For each tool the host names one argument as the target ID. All other arguments are the payload,
described by the queue's item schema. The write function takes the same arguments as the tool.
`submit` still refuses a payload that contains the target ID, so free text that repeats it stops
that call with a clear error. v1 wraps synchronous write functions. Database connections belong
to one thread and some libraries run tools on other threads, so adapters take a function that
opens an `Oversight` for each use.

### 7. Rejections and edits

*For Microsoft Agent Framework, the rejection message is
[amended](#amendment-microsoft-agent-framework).*

- **Reject:** nothing runs. The agent gets a fixed message: a reviewer did not approve this
  action, and it was not done.
- **Edit:** because ReviewProof stores no content, and two libraries cannot reliably carry an
  edit back, the host gives the adapter an `approved_payload(key)` function that reads the
  reviewer's version from the host's review application. Without it, the proposal is used
  unchanged, so an edited item is refused by the guard and nothing runs. After an edited write,
  the agent is told that it was done with reviewer changes, and which fields changed, so it
  does not assume its proposal was written.
- A decision on a planted item never sends anything to the agent.

### 8. Packaging, versions and tracing

- Each adapter is an optional extra, such as `reviewproof[langgraph]`. The core still depends
  only on PyYAML and does not import any of these libraries.
- ReviewProof's core stays on Python 3.10. **The Pydantic AI and Agent Framework adapters need
  Python 3.11**, as the current releases of those libraries do. Older releases that install on
  3.10 are not supported.
- Each extra's lowest version is the one tested above. CI runs each adapter against that version
  and the newest one.
- ReviewProof makes no network calls and **changes no tracing settings**. The guides say what
  each library sends by default. The OpenAI Agents SDK guide says how to turn tracing off or
  leave sensitive data out, and that a host handling regulated data must decide this first.

### 9. What a deployment must keep from reviewers

*[Amended](#amendment-of-2026-11-27) to cover the review queue.*

A planted item exists only in the review queue. A reviewer who can see the agent's traces,
saved state or logs, or the target system as it changes, can tell planted from real. So can a
reviewer who sees **whether the agent continues after a decision**: it continues only after the
real item. If the other item of the pair is still waiting, they then know it is planted.
**Reviewers must not be able to watch any of these while items are waiting.** This rules out
having the person who is using the agent review its actions as they work. ReviewProof cannot
enforce this.

## Alternatives not chosen

- **Trust the library's approval and only add planted items to it.** A standing approval or a
  second resume would then write without review, and planted items would enter agent state.
- **Mark the real ticket for the host.** A marker on a ticket can reach the review screen.
- **Save the tickets in the agent's state, to survive replays.** Planted content would be in
  checkpoints.
- **Store edited payloads in ReviewProof.** It would then hold regulated content.
- **Pass edits through each library.** It does not work in all five.

## Consequences

- This changes `submit`, the registry and the guard's entry points, which are the safety core.
  The Gate 1 tests are extended before any adapter is written: a repeated key never draws again;
  a changed payload is refused; a planted item cannot run through `settle` or `execute_call`;
  resuming without a decision, or twice, writes at most what was approved, once.
- A validated user would need to retest the gate and the guard.
- The host still has work to do: a review screen, an idempotent `enqueue`, a call to `settle`,
  resuming the agent, and for edits, `approved_payload`.

## Amendment of 2026-11-27

**Accepted. Found while building the LangGraph adapter.** Observed with `langgraph` 1.2.24
and `langgraph-prebuilt` 1.1.3, as before.

**What was missed.** The first scripts proposed one call. With two reviewed calls in one step,
resuming for the second ran the step again and reached the first write. The guard would refuse
it, so nothing is written twice, but the run breaks and the second approved call never runs.
This happened in a hand-written step and in LangGraph's prebuilt tool node.

1. **Every adapter must give each reviewed call its own replayable step.** The shared tests now
   propose two calls at once and require each to run once.
2. **LangGraph: ReviewProof provides the step.** `ReviewStep` takes one call, sent with
   LangGraph's `Send`; given anything else it refuses before submitting. Its errors stop the
   run, which settles section 5 for this adapter. Using a `ReviewedTool` inside the prebuilt tool
   node is **not supported**: by default that node does stop the run on a failed write, but with
   `handle_tool_errors=True` it turned the failure into text for the model, and calls can share
   its step. Saving each write as a LangGraph task also worked in a trial, but showed one pause
   at a time and would rely on calls being matched by order after a restart. On Python 3.10
   the adapter works with `invoke` only: plain LangGraph cannot pause inside `ainvoke` there.
   The adapter refuses it before anything is submitted.
3. **The pause names the tool and the key.** This replaces "the pause says only that a review is
   pending" in section 4. The host needs the key to resume the right pause. It holds no ticket
   and no content, and the review screen is still built only from tickets.
4. **Section 9 also covers the review queue.** In a quiet queue, the agent's next action
   arriving right after a decision shows that the decided item was real. The guides tell hosts to
   resume on a schedule unless the queue is busy enough to hide this. In a very quiet queue a
   schedule alone may not hide it: if only one decision was made since the last resume, what
   arrives next still points to it.

## Amendment: OpenAI Agents SDK

**Accepted 2026-11-27. Found while building that adapter.** Observed with `openai-agents` 0.26.0 and a
scripted model.

**What was missed.** Section 5 treats stopping the run as settled by
`failure_error_function=None`. It covers a tool's ordinary errors, but not these, each of which
told the model something other than what happened and let the run continue:

| Setting or event | The model was told |
| --- | --- |
| A time limit on the tool, with its default behaviour | The call timed out. The write still happened, afterwards. |
| An output guardrail that replaces the result | The result was blocked. The write had happened. |
| A hand-built tool raising a cancellation | An error occurred while running the tool. |

1. **ReviewProof builds the reviewed tools.** A reviewed tool with a time limit or an output
   guardrail is refused, at the pause and again just before a write. A cancellation raised by
   the write function is turned into an error that stops the run.
2. **The host must resume from the newest saved state.** The SDK keeps each finished call's
   result in the state, so each call can resume on its own: that is this library's replayable
   step. Resuming an older state ran a finished call again, which the guard refuses.
3. **After a failed write the host must not carry on from a saved state.** Calls approved
   together run together, so another may have written although the run returned no result.

## Amendment: Pydantic AI

**Accepted 2026-11-27. Found while building that adapter.** Observed with `pydantic-ai-slim` 2.76.0 and
a scripted model.

**What was missed.** Section 5 counts stopping the run as confirmed, from a `ConnectionError`.
Other failures inside an approved tool told the model something and let the run continue:

| Failure or setting | What happened |
| --- | --- |
| A Pydantic `ValidationError` raised inside the tool, for example while checking a reply after writing | Its text went to the model. |
| `ModelRetry` or `ToolFailed` raised inside the tool | Its text went to the model. |
| A time limit on the tool or the whole agent | The model was told the call timed out. The write finished afterwards. |
| A host hook that recovers from tool errors | The model got the hook's replacement for an ordinary error. |
| A host hook that raises `ModelRetry` after the tool succeeded | The model was told to try again. The write had happened. |

1. **Every failure of a reviewed tool is raised as `RunStopped`, which is a `BaseException`, not
   an `Exception`.** `KeyboardInterrupt` and `SystemExit` pass unchanged. A hook for tool errors
   does not catch it. With two or three calls failing at once in tests, the run ended and the
   model was not asked again. **The cost:** a host's `except Exception` does not catch it, so the
   host must catch it by name, and frameworks around the host may treat it as more serious than
   an ordinary error. The alternative, an ordinary exception with a rule against such hooks,
   would let a hook added later turn a failed write into a success message.
2. **ReviewProof builds the reviewed tools, with an unlimited time limit of their own,** which
   comes before the agent's. Just before writing, the tool reads the limit in force and refuses
   any other.
3. **Hooks that act on a reviewed tool's result are not supported.** ReviewProof cannot detect
   or stop them. The guide says so.
4. **A run resumes only when every call it waits on has been decided,** because the library
   takes all answers together. The replayable unit is the whole group: calls approved together
   run together, so after a failure others may have written. The shared tests allow a library
   that works this way, and require that nothing runs until all are decided. The host must keep
   only the newest messages and pause.

## Amendment: Strands Agents

**Accepted 2026-11-27. Found while building that adapter.** Observed with `strands-agents` 1.67.0 and a
scripted model. Section 5 left stopping the run open for this library.

| Observed | What happened |
| --- | --- |
| A tool raised an ordinary error | Its text went to the model, and the run continued. |
| With the default executor, which runs tools together, a tool raised an error outside `Exception` | **It was lost.** The run continued and the model got no result for that call. |
| A check only before the model is asked | A failure while another call was still waiting looked like an ordinary pause. The same happened when the run ended without asking the model. |
| Background tasks on | The model was answered before the write finished. |
| A retry hook | It ran the tool again. |

1. **Stopping takes two checks.** A reviewed tool raises nothing. It marks the agent as failed,
   in memory and in the agent's saved state, and ReviewProof's hooks raise `RunStopped` both
   before the model is asked and when an invocation ends. The history says only that the action
   did not complete. A failed agent stays failed, and its reviewed tools write nothing more.
   `RunStopped` is an ordinary exception here, because the hooks raise it, not the tool. This
   settles section 5 for this library: in tests the model was not asked again after a failure.
2. **`ReviewTools` is also the hook provider, and the tools refuse to write unless the call
   passed its pause.** Without the hooks nothing can stop the run: nothing is written, but the
   model is told that the call did not complete. A tool that ReviewProof did not build, under a
   reviewed name, is stopped before it runs.
3. **Background tasks are refused.** This reads a private setting of the agent, because the
   library offers no public one. If a release renames it, the test against the newest release
   fails.
4. **Each call pauses and resumes on its own.** A hook raises the pause, because a tool built
   from the host's own argument schema was not found to be able to pause through public
   interfaces. Saved sessions normally remember a failure. When a failed run was about to pause
   for another call, the session can be the one from the earlier pause; resuming it ran the
   failed call again, which the guard refused. The host must keep its own record of stopped runs.
5. **Hooks or middleware that rewrite a reviewed tool's result are not supported.**

## Amendment: Microsoft Agent Framework

**Accepted 2026-11-27. Found while building that adapter.** Observed with `agent-framework-core` 1.26.0
and a scripted model. Section 5 left stopping the run open for this library.

| Observed | What happened |
| --- | --- |
| A tool raised an ordinary error | The model was told the function failed, and the run continued. |
| A tool raised the library's `MiddlewareFailure` | The run stopped, but a host middleware that recovers from errors caught it, and the model got that middleware's result. |
| A tool raised an error outside `Exception` | The run stopped with no middleware, with a passing one and with a recovering one, with two calls at once, and when streaming. |
| Two calls waiting, one answered | Nothing ran until both were answered. |
| An approval answered without a session | It was ignored. Nothing ran, and the model carried on. |
| One saved session restored twice | The tool ran twice. |
| "Always approve" through the approval middleware | A later call ran with no pause. |

1. **Every failure of a reviewed tool is raised as `RunStopped`, a `BaseException`,** as for
   Pydantic AI and with the same cost: a host's `except Exception` does not catch it.
   `MiddlewareFailure` is not used. This settles section 5 for this library.
2. **ReviewProof builds the tools.** The tool takes each call's ID from the library's `invoke`
   method, and any error after its function has started also stops the run. A failed session's
   reviewed tools write nothing more. The library would still ask the model, so the host must
   not use the session again.
3. **A session is required.** Its ID and the tool-call ID are the key, so no run ID function is
   needed. ReviewProof cannot raise an error for a pause answered without a session, because
   its tool is never called: nothing is written, but the model carries on.
4. **A run resumes only when every call has been decided,** as for Pydantic AI, with the same
   duties after a failure.
5. **For a rejection, the model is told the library's own fixed message,** "Error: Tool call
   invocation was rejected by user.", which replaces the one in section 7. Approving the call in
   the library so that ReviewProof's message could be returned would run the tool for a
   rejected call, and record it there as approved.
6. **Middleware that replaces a reviewed tool's result is not supported.**
