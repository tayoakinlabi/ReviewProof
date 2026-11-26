# ADR-003: API shape, planted errors and the guard

- **Status:** Accepted
- **Date:** 2026-11-25
- **Amended:** 2026-11-26 (what the reserved target range protects against; planted errors
  are reused; a due planted error is released at once, not after a delay)

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
                                                         # edited_fields and final_payload
ov.execute(token, payload=final_payload, executor=write_to_qms)   # executor(target_id, payload)
```

A ticket holds only an opaque random token and the payload. It has no planted marker and no
target ID. The host calls `execute()` for every approved or edited ticket in the same way.

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

Any error while checking, including an unavailable registry, means **nothing runs**.
`execute_requested` is logged before the executor is called, so each item runs at most once. After
a crash between the two, a person investigates; the guard does not retry.

An approved planted error is a no-op, logged as `blocked` then `withdrawn`. The host does not need
the payload to be stored: it supplies it again at execution.

### Scoring a planted error

- **Catch:** the decision is `reject`, or it is `edit` and the edited fields include
  `defect_field`.
- **Miss:** anything else, including an edit that leaves the defect field unchanged.

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
