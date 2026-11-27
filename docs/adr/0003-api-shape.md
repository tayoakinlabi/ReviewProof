# ADR-003: API shape, planted errors and the guard

- **Status:** Accepted
- **Date:** 2026-11-25
- **Amended:** 2026-11-26 (what the reserved target range protects against; planted errors
  are reused; a due planted error is released at once, not after a delay; ticket field order;
  edits are worked out by ReviewProof; execution results and failures, feedback, context
  changes, adapters, and what a review screen may show)
- **Also amended by:** [ADR-005](0005-adapters.md) (repeating `submit` safely, three methods
  for paused agents, and what server code can learn about a pair)

## Context

The host keeps its own review UI and executor and calls four methods: submit, shown, decided,
execute. A planted error must look like any other item, must never execute, and must never delay or
replace real work.

## Decision

### Submit never replaces a real item

`submit()` always registers the real item. It returns a **list of tickets**: the real one, and
sometimes a planted one, in random order. The host puts every ticket in the review queue and
renders `ticket.payload`.

```python
tickets = ov.submit(Item(payload=proposal, target_id=record_id, trace=run_trace),
                    reviewer="r-104")       # only if the host already knows
for ticket in tickets:                      # one or two; the host cannot tell which is which
    review_queue.put(ticket.token, ticket.payload)

ov.shown(token, reviewer="r-104", context=shown_manifest)
ov.decided(token, reviewer="r-104", decision="approve")   # an edit also passes
                                                         # final_payload
result = ov.execute(token, payload=final_payload,
                    executor=write_to_qms)   # executor(target_id, payload)
```

A ticket holds only an opaque random token and the payload. It has no planted marker and no
target ID. The host calls `execute()` for every approved or edited ticket in the same way.
The two rows and events of a pair are also written in random order.

**Field order.** Every ticket's payload, real or planted, is rebuilt in the order the item
schema lists its properties, then by name. Without this, a planted item stored with sorted
fields would reach a screen in a different order from real items, which keep the host's
order. If the schema cannot be loaded, nothing is planted.

**What a review screen may show.** Everything a reviewer sees about an item must come from
its ticket's payload. A planted item has no agent trace, retrieved sources or record
history behind it, so a screen that shows those for real items would expose every planted
one. `shown(context=...)` records the names of what was on screen. In v1, a host whose
screen must show material beyond the payload cannot use planted errors on that queue.

**Real work comes first.** A real item that does not match the schema is accepted, with a
warning. If the planted-error side fails, for example because the library is damaged, the
real item is accepted without a planted one and an error is logged that names no planted
error. The databases themselves must be available: without them, submission fails.

**The host must not reveal which tickets arrived together.** When two arrive at once, a
reviewer who can see that knows one of them is planted. The host must not show when an item
arrived, and should not list items in the order it received them without mixing them with
others. The SDK cannot enforce this.

The decorator and LangGraph adapters, which expect one result per call, enqueue the tickets
through a host-supplied callback.

### Planted errors are user input

Users write each planted error as a YAML or JSON file holding the defective payload,
`defect_class`, `defect_field` and `expected_decision`. The tool never samples or copies live
items. Users write them with synthetic or de-identified values.

- The payload must pass the queue's item schema, exactly as a real item would. An omission defect
  therefore removes content the schema does not require, such as one entry from a list. A payload
  that breaks the schema would be easy to spot.
- `defect_field` is a top-level property named in the item schema.
- Identifiers shown in the payload, such as a deviation number, must look like real ones and must
  not collide with them.

### Recording a review

- `shown()` may happen more than once, and for more than one reviewer, until a decision.
- `decided()` happens once per item, by a reviewer for whom `shown()` was recorded.
- An edit passes `final_payload`, the whole item as edited. ReviewProof works out which
  top-level fields changed by comparing field hashes (ADR-002), and does not rely on the
  host to say. `edited_fields` is optional and, if given, must agree. An edit that changes
  nothing is refused.
- Every refusal is the same for real and planted items.

### Target IDs stay inside the guard

The host passes a real item's `target_id` to `submit()`. The guard stores it and hands it to the
executor only when the item is real and was approved or edited. Item payloads must not contain
target IDs, so no review screen shows them.

Planted errors get an ID from a reserved range that the host's executor must reject. This is a
backstop against **a fault in ReviewProof**: if a planted item were ever wrongly treated as
real, the executor would receive a reserved ID and refuse it. The guard also rejects a real
item submitted with an ID in that range.

It does **not** protect against a host that bypasses `execute()`. Such a host never receives
the reserved ID, so there is nothing for its executor to reject. The earlier text of this
record said otherwise. Bypass is covered only by the integration rule and by a test in the
host's own environment.

### Execute

`execute()` calls the executor only if **all** of these are true:

1. The token exists and the item has a decision of `approve` or `edit`.
2. The registry has a row for the item, it says `real`, and it matches the commitment (ADR-002).
3. The target ID is not in the reserved range.
4. The payload's hash equals the hash of the submitted payload, or, for an edit, the hash of the
   final payload passed to `decided()`. A payload changed after approval is not executed.
5. The item has no earlier `execute_requested` event.

Any error while checking, including an unavailable registry or one nobody foresaw, means
**nothing runs**. The events relied on are checked first (ADR-002), and there must be exactly
one decision. Check 3 also looks for the ID among all planted errors' reserved IDs, so it does
not depend on the registry's `real` being right or on the configured prefix. Every check that
reads a database comes before `execute_requested`, so an outage never uses up an item's one
attempt. Just before the executor is called, the prefix test is repeated without any database.

`execute_requested` is logged before the executor is called, so each item runs at most once.
After a crash between the two, a person investigates; the guard does not retry. **An executor
that raises is not retried either**, because it may have written part of the change. This is
logged as `execute_failed`, with the type of error only. A refused check, such as a changed
payload or an outage, does not use up the one attempt.

`execute()` returns whether the executor ran and what it returned. It raises `GuardError`
when a check fails, meaning nothing ran. If the executor ran but that cannot be logged, it
raises `UnrecordedExecutionError`, which is deliberately a different kind of error. Error
messages and the host's logs never name a planted error.

An approved planted error is a no-op, logged as `blocked` then `withdrawn`. The host does not need
the payload to be stored: it supplies it again at execution.

### Scoring a planted error

- **Catch:** the decision is `reject`, or it is `edit` and the fields ReviewProof found
  changed include `defect_field`. This shows the field was changed, not that the new value
  is right.
- **Miss:** anything else, including an edit that leaves the defect field unchanged.

### Feedback

`feedback(reviewer)` returns the outcome of each planted error that reviewer has decided on,
once each, and logs `feedback_sent` first. On an `immediate` queue the host calls it after
each decision. On a `batched` queue it returns nothing unless the host's scheduled job passes
`batch=True`. Feedback shows the planted item, so with reuse a reviewer could describe it to
colleagues; QA should weigh that when choosing the timing.

### Changes to the AI system

A change of model ID, model version, prompt version or tool-schema hash is a new context,
whether made with `register_system()` or in the settings file. Going back to an earlier one
counts too. Everyone who reviewed or qualified under another context of that system is
marked `requalification_required`, and `context_registered` lists them. The settings file
wins when ReviewProof next opens. Status is one value per reviewer; per-system
qualification comes with qualification mode.

### Adapters

`run_review()` takes each ticket through an approval function that answers at once. It asks
twice when two tickets come back; the host must not show where one submission ends. The
`gate` decorator passes every ticket to a callback.

### Reusing planted errors

Planted errors are reused. The gate chooses only among those that are approved, not retired
and still fit the item schema, and:

- never one with a copy still waiting for a decision, because two identical items in a queue
  would give both away. A copy that is never decided keeps its planted error held back;
- if `submit()` names a reviewer, never one that person has been given, shown or decided on.
  A host that names a reviewer must send **every** returned ticket to that person;
- among the rest, one of the least used, chosen at random.

**Without a reviewer, a repeat cannot be prevented.** Colleagues can also tell each other
about an item. So scoring counts only a reviewer's **first** sight of each planted error.
Later sightings are reported separately. This can be rebuilt from the log and the verified
registry.

### Planted rate

The configured rate is the planted share of **all items shown**. With each real item
submitted, the gate releases a planted error with probability `p / (1 - p)`. The draws are
independent and made with the operating system's secure random source.

An earlier version of this record delayed each release by 1 to 5 submissions, one at a
time. Testing showed that this bunched releases: right after one, another was 15 to 25%
more likely than average, and then less likely for the next two. The delay also protected
nothing, because a planted error has no connection to the real item it arrives with.

If a planted error is due and none can be used, it is not saved up, which would cause a
burst later. The miss is recorded so that QA can see the queue ran short.

Independent draws mean the share over a short stretch varies. At 2% over 10,000 items, one
standard deviation is about 0.14 percentage points, and over 1,000 items about 20 planted
errors are expected, but 12 or 28 would not be remarkable.

## Consequences

- Every host must handle `submit()` returning more than one ticket.
- A queue with too few approved planted errors will fall below its configured rate. The tool must
  report this clearly.
- A pilot cannot rely on an exact number of planted errors in a short period.
- Fewer planted errors need writing than if each were used once, at the price of possible
  repeats where the host cannot name the reviewer.
- The result of `execute()` tells the host that an item was blocked. This happens after the
  decision, but the host UI must not show it to other reviewers.
- The SDK cannot stop a host from calling its executor directly; the validation pack tests this in
  the host's own environment.
