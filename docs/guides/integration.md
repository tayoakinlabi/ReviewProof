# Integrating ReviewProof

For the engineer who connects ReviewProof to an application.

> **Status:** ReviewProof is pre-release and not pre-validated.

## Rules that keep planted errors safe

ReviewProof cannot enforce most of these. Your application must.

1. **Act only through `execute()`.** Never call your executor yourself with an item's content.
   A host that does so can execute a planted error, and nothing in ReviewProof can stop it.
2. **Make your target system refuse the reserved target range** (`reserved_target_prefix` in your
   settings). This protects you if ReviewProof itself is ever wrong. It does **not** protect you
   if you bypass `execute()`, because only `execute()` hands out target IDs.
3. **Queue every ticket `submit()` returns**, and treat them all the same.
4. **Do not let reviewers see which items arrived together.** When two arrive at once, one is
   planted. Show no arrival times, and mix new items among those already waiting.
5. **Show only what is in `ticket.payload`.** A planted item has no agent trace, retrieved
   sources or record history. If your screen shows such things for real items, every planted one
   stands out. In v1, a queue whose screen must show them cannot use planted errors.
6. **Behave the same whatever `execute()` returns.** `executed` is false for an item that must
   never run. Do not show reviewers a different message or status.
7. **If you name a reviewer in `submit()`**, send every returned ticket to that person.
8. **Keep reviewers away from both database files.** `planted.db` is the restricted one: give
   write access only to the application and QA administrators.

## Set up once

```python
from reviewproof import initialise

initialise("oversight.db", "planted.db")
```

If one of the two files is ever lost, restore **both** from the same backup. `initialise()`
refuses to create a new file beside an old one.

### After upgrading ReviewProof

If a release changes the database format, ReviewProof refuses to write to your old
databases until you upgrade them. Stop the application, **back up both databases**, then
run this once, as a deliberate step under your change control:

```bash
rp init --db oversight.db --planted planted.db
```

An upgrade cannot be undone: earlier releases cannot open an upgraded database. In Python,
`initialise(..., upgrade=True)` does the same. Do not put that in code that runs every time
your application starts.

QA then writes and approves planted errors, using the
[authoring guide](writing-planted-errors.md). The settings file is shown in the
[demo](../../examples/deviation_triage/reviewproof.yaml).

## Around your approval step

```python
from reviewproof import Item, Oversight

reserved = "rp-reserved-"
written = []


def write_to_qms(target_id, payload):
    if target_id.startswith(reserved):
        raise PermissionError("reserved target ID")
    written.append(target_id)


with Oversight("examples/deviation_triage/reviewproof.yaml", "deviation-triage") as oversight:
    proposal = {  # what your AI system produced, in your item format
        "deviation_id": "DEV-2026-0301",
        "occurred_on": "2026-07-01",
        "product": "Examplol 50 mg tablets",
        "lot_number": "LOT-A10301",
        "process_step": "dispensing",
        "description": "A synthetic example.",
        "classification": "minor",
        "classification_rationale": "A synthetic example.",
        "root_cause": "A synthetic example.",
        "immediate_actions": ["Recorded the event"],
        "capa_required": False,
    }
    tickets = oversight.submit(Item(payload=proposal, target_id="QMS-301"))

    for ticket in tickets:  # in a real application, this happens later, in your review screen
        oversight.shown(ticket.token, reviewer="r-104")
        oversight.decided(ticket.token, reviewer="r-104", decision="approve")
        oversight.execute(ticket.token, payload=ticket.payload, executor=write_to_qms)

assert written == ["QMS-301"]
```

- **Without a `key`, call `submit()` only once for each proposal.** Every such call creates
  a new item and another chance of a planted error. If your code can run again, as agent
  libraries do when they resume, see [agents that pause](#agents-that-pause-and-resume).
- Keep each `ticket.token` with the item in your queue. It is a secret: do not log it.
- For an edit, pass the whole edited item as `final_payload` to `decided()`, then the same item
  to `execute()`. ReviewProof works out which fields changed.
- Do not call `execute()` for a rejected item.
- `shown()` takes an optional `context` naming the sources, steps and fields on screen. Pass
  names only, never content.
- `submit()` takes your agent's step record as `Item(trace=...)`: a list of steps with
  `step_id` and `step_type`, or OpenTelemetry-style spans. Only IDs, types, times and hashes
  are kept.
- One `Oversight` instance belongs to one thread. Give each thread or process its own.

If your approval step is a function that answers at once, `reviewproof.adapters.run_review` does
all of this ([example](../../examples/deviation_triage/integrate.py)). If a function makes
proposals for later review, `oversight.gate(...)` wraps it.

## When `execute()` raises

| Error | Did anything run? | What to do |
| --- | --- | --- |
| `UnknownTokenError`, `TransitionError` | No | Fix the call. A rejected, undecided or already requested item never runs. |
| `OversightError` | No | Fix the arguments. |
| `GuardError` | No | A safety check failed. Run `rp verify` and find out why. |
| `UnrecordedExecutionError` | **Yes** | Do **not** repeat it. Check the target system and the log. |
| Whatever your executor raised | **Perhaps in part** | Check the target system. ReviewProof will not run the item again. |

**There is no automatic retry.** An item is requested for execution at most once, so a failed
write needs a person to check it and, if appropriate, make the change in your target system
under your own procedures. Retry temporary network errors **inside** your executor, if your
target system makes that safe. A `GuardError` caused by an outage or a changed payload does not
use up the one attempt.

## Agents that pause and resume

Agent libraries pause before a tool runs and resume it later. Between the two they may run code
again, resume twice or approve by themselves. So **the library's approval only wakes the tool;
ReviewProof decides what may be written.** There is an adapter for
[LangGraph](langgraph.md). Adapters for other libraries are not included yet. `ReviewedTool`
is the part they share, and you can use it directly:

```python
from reviewproof import CallState
from reviewproof.adapters.tool import ReviewedTool

settings = "examples/deviation_triage/reviewproof.yaml"
review_queue = {}


def save_record(record_id, **fields):  # your write function, which the agent must never get
    written.append(record_id)
    return "saved"


tool = ReviewedTool(
    lambda: Oversight(settings, "deviation-triage"),  # a new one for each use
    save_record,
    target_argument="record_id",  # every other argument is the item that is reviewed
    enqueue=lambda ticket: review_queue.setdefault(ticket.token, ticket.payload),
)
arguments = {**proposal, "deviation_id": "DEV-2026-0302", "record_id": "QMS-302"}
key = "run-7/call-1"  # your run or thread ID, and the library's tool-call ID

# 1. Before the agent pauses.
tool.submit(key, arguments)

# 2. In your review application, after EVERY decision.
with Oversight(settings, "deviation-triage") as oversight:
    for token, payload in review_queue.items():
        oversight.shown(token, reviewer="r-104")
        oversight.decided(token, reviewer="r-104", decision="approve")
        status = oversight.settle(token, payload)

# 3. When status.state is no longer waiting, resume the agent. The resumed tool does this.
assert status.state is CallState.APPROVE
outcome = tool.run(key, arguments)  # give outcome.reply to the agent
assert outcome.reply == "saved"
assert written == ["QMS-301", "QMS-302"]
```

- **One reviewed call to each step that can be replayed.** If your library runs a paused step
  again, a step with two reviewed calls reaches the first write again when it resumes for the
  second. The guard refuses it, and the second approved call never runs.
- **Keys.** A key names one proposed action and must be the same each time that code runs. The
  same key with a different target, content or reviewer is refused, so build the proposal
  before the step that pauses, not inside it.
- **`enqueue` is called on every `submit()`**, including repeats. It must ignore a token your
  queue already has.
- **Call `settle()` after every decision**, with the payload as decided (a rejection needs
  none). It handles planted items and returns the state of the real action. It, and
  `call_status(key)`, are for your server code only.
- **Resume each paused call once.** Both decisions of a pair can report that the real action is
  decided, so keep a note of the keys you have resumed. A second resume writes nothing.
- **Edits.** ReviewProof stores no content. Give `ReviewedTool` an `approved_payload(key,
  proposal)` function that returns the reviewer's version from your review application.
  Without it, an edited action is refused and nothing runs.
- **Let every error from `run()` stop the agent's run.** Never turn one into a tool result: the
  agent could try again, after a write that may have partly happened. Some libraries do this by
  default, so check yours.
- **Build the review screen only from tickets,** never from the library's approval request.
  Approval inside the agent's own chat is not supported.
- **Check what your agent library records or sends.** Some send tracing data to their vendor by
  default.

### What reviewers must not see

A planted item exists only in your review queue. While items are waiting, a reviewer **must
not be able to watch** the agent's traces, saved state or logs, the target system as it
changes, or **whether the agent continues after a decision**. Any of these can show which item
is planted. So the person using an agent cannot be the one who reviews its actions as it works.
The review queue can show it too: in a quiet queue, the agent's next action arriving right
after a decision shows that the agent continued. Resume agents on a schedule, not straight
after each decision, unless the queue is busy enough to hide the connection. In a very quiet
queue a schedule alone may not hide it. ReviewProof cannot enforce any of this: your design
and your procedures must.

## Qualification sessions

`oversight.qualification()` runs seeded-defect sessions away from the live queue. Show each
session item on your review screen as you would a live one, and never call your executor for
it. See [qualifying reviewers](qualification.md).

## Feedback to reviewers

`oversight.feedback(reviewer)` returns the outcomes of planted errors that reviewer has decided
on, each only once. For a queue set to `immediate`, call it after each decision. For `batched`,
run a scheduled job that calls `oversight.feedback(reviewer, batch=True)`.

## When your AI system changes

Call `oversight.register_system(model_version=..., prompt_version=...)`, and update the settings
file too: the file wins the next time ReviewProof opens. Reviewers who worked under another
version are flagged for requalification.

## Checking the evidence

```bash
rp verify --db oversight.db --planted planted.db --export-head head-2026-11-26.json
rp verify --db oversight.db --planted planted.db --expect-head head-2026-11-26.json
```

Run the first on a schedule, and keep each exported file **where ReviewProof cannot write**.
Back up both databases together.

## Reports and alerts

QA staff do not need a terminal: schedule these and send them the results.

```bash
rp report --since 7d --out report.html     # one page for the QA lead
rp alerts --since 30d --send               # to the QA lead set in the settings
```

`rp report` exits with status 1, and shows no scores, if the evidence does not verify. Make
your scheduled job treat any status other than 0 as something to look at. Send reports with
individual results (`--individuals`) only to QA. See the [command reference](../cli.md) for
the settings and for how every figure is defined.

## Test it in your own environment

Do this before go-live, and after any change to your review screen or executor. In a **test**
environment, with synthetic planted errors:

1. Set the queue's `planted_rate` to `0.5`.
2. Submit at least 50 synthetic items and **approve every item** in your own review screen.
3. Check in the **target system itself** that no planted error arrived: search for the planted
   items' identifiers.
4. Stop access to `planted.db` and approve more items. Check that **nothing** reaches the
   target system.
5. Look at the review screen as a reviewer would: can you tell which items are planted?
