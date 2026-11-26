# ADR-003: API shape, planted errors and the guard

- **Status:** Accepted
- **Date:** 2026-11-25
- **Amended:** 2026-11-26 (what the reserved target range protects against)

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
tickets = ov.submit(Item(payload=proposal, target_id=record_id, trace=run_trace))
for ticket in tickets:                      # one or two; the host cannot tell which is which
    review_queue.put(ticket.token, ticket.payload)

ov.shown(token, reviewer="r-104", context=shown_manifest)
ov.decided(token, reviewer="r-104", decision="approve")   # an edit also passes
                                                         # edited_fields and final_payload
ov.execute(token, payload=final_payload, executor=write_to_qms)   # executor(target_id, payload)
```

A ticket holds only an opaque random token and the payload. It has no planted marker and no
target ID. The host calls `execute()` for every approved or edited ticket in the same way.

A planted ticket must not always sit next to the submission that produced it. The gate therefore
decides *that* a planted error is due and releases it with a later submission, after a random
delay. Hosts that show items in strict arrival order get the same protection.

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

A planted error is shown at most once to the same reviewer.

### Planted rate

The configured rate is the planted share of **all items shown**. For a rate `p`, the gate makes a
planted error due with probability `p / (1 - p)` for each real item submitted.

## Consequences

- Every host must handle `submit()` returning more than one ticket.
- A queue with too few approved planted errors will fall below its configured rate. The tool must
  report this clearly.
- A delayed planted error that is due when the queue goes quiet is released with the next
  submission.
- The result of `execute()` tells the host that an item was blocked. This happens after the
  decision, but the host UI must not show it to other reviewers.
- The SDK cannot stop a host from calling its executor directly; the validation pack tests this in
  the host's own environment.
