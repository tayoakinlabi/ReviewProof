# ADR-010: Changes from the first use case

- **Status:** Accepted
- **Date:** 2026-11-28

## Context

Building a use case with release 0.2.0 from PyPI, with made-up data and simulated reviewers,
showed four problems. **It was not used with real reviewers or on a real queue.**

1. When a simulated reviewer edits a correct proposal, the
   [simulator](../../src/reviewproof/simulator.py) changes its longest text property. In the
   use case that was the text of the incoming request, which a reviewer would never rewrite.
2. The [integration guide](../guides/integration.md) and deployment duty DEP-003 forbid showing
   which items arrived together, when, or in what order. **They say nothing about counts of
   items,** such as "3 of 10" or "5 waiting". Review tools commonly show them. Test script MT-004
   has the tester inspect counts, but only for what they show about arrival.
3. A report showed 18 planted errors "ready to use" beside 6 times when one "was due but none
   could be used". Both were right: the named reviewers had had all 18. The report does not say
   how both can be true.
4. ReviewProof has no way to say that part of an item must not be edited.

## Decision

Fix 1 to 3, with no database change and no change to what the gate, guard or log do. Release
them as 0.3.0.

### 1. Let the caller say which text a simulated edit may reword

`SimulatedReviewer` takes a new optional argument, `reword`: the names of the properties that a
simulated edit to a **correct** proposal may change.

- Without it, behaviour is as now, so existing code is unaffected.
- With it, the longest text among the named properties is changed. An empty list is refused
  when the reviewer is made. An item with no text in any named property raises an error, as an
  item with no text at all does now.
- A name that the item does not have is ignored for that item, because some properties are
  optional.
- **Name free-text properties only.** The simulator adds words to the text, which could break a
  list of allowed values or a pattern. It does not check the item format.
- It does not affect planted errors: a simulated catch still changes the defect field to the
  correct value, or rejects.

The simulator is for demonstrations and testing. Its behaviour is assumed, and this does not
change that.

### 2. Show reviewers no counts of items

**Integration guide,** added to rule 4:

> **Show no counts of items:** waiting, in a batch, decided or saved. A count that includes
> planted items can be compared with the number of real ones, which shows how many are planted.
> A count that leaves them out does not change when a reviewer decides a planted item, which
> shows which one it was. A reviewer can still count for themselves; this removes the easy ways.

**DEP-003** becomes:

> Queue every ticket, and never show reviewers which tickets arrived together, when each
> arrived, or the order they arrived in. Show them no counts of items waiting, in a batch,
> decided or saved, whether or not planted items are in the count.

**Test script MT-004, step 1,** expected result becomes:

> Nothing shows when a ticket arrived, which arrived together, or the order of arrival. No
> count of items waiting, in a batch, decided or saved is shown anywhere the reviewer can reach.

**ReviewProof cannot enforce this.** It follows from how the counts behave and has not been
tested with reviewers.

### 3. Say how planted errors can be ready while none can be used

In the HTML report, **only when the shortage count is above zero,** under "Health of the
planted-error programme":

> Some can be ready while none can be used. A planted error is not used while a copy of it
> waits for a decision, or, if your application named the reviewer, for a person who has
> already had it. The report does not know which reason applied each time.

The [planted-error procedure](../../validation-pack/sop-planted-errors.md#8-watching-the-stock),
section 8, gives the same two reasons beside the shortage sign. The CSV report is unchanged.

**It gives reasons, not the cause.** `planted.db` records only the queue and time of each
shortage. Saying why each happened would need a new column.

## Alternatives not chosen

- **Never let a simulated reviewer edit a correct proposal.** A simulation without such edits
  would be less like review.
- **Guess which properties are free text from the item format.** A request and a proposed reply
  can both be free text. Only the caller knows which a reviewer may change.
- **Allow counts if your risk assessment accepts them.** Many review tools show queue sizes, so
  a flat rule may be hard to meet. It is proposed anyway because either kind of count gives
  information away. An organisation that shows one should record it as a deviation from
  DEP-003 and assess what its results are worth.
- **Record the reviewer or reason for each shortage.** It would answer "why", but needs another
  upgrade of `planted.db`, and a reviewer ID there is more personal data to protect.
- **Count, when the report is made, the reviewers who have had every ready planted error.** It
  describes the present rather than the time of each shortage, and with one reviewer it
  describes a person.
- **Read-only properties (problem 4).** Deferred. It would touch the item format, what an edit
  may record and perhaps scoring, and needs its own design. Until then, your review screen has
  to prevent such edits.

## Consequences

- **No database change.** `oversight.db` and `planted.db` stay at schema version 4. Existing
  logs, reports of past data and code written for 0.2.0 are unaffected, except for the added
  note in new HTML reports.
- **DEP-003 and MT-004 are stricter.** Anyone who adapted the validation pack must check their
  review screen again, and the release notes must say so.
- No software requirement changes: the simulator has none, and DEP-003 is a deployment duty.
- Release 0.3.0 also needs a changelog entry with a change-impact note, README links pinned to
  `v0.3.0`, and a statement that **no outside quality specialist has reviewed it and it has not
  been tested with independent reviewers or on a real queue.**

## Checks before accepting an implementation

- Without `reword`, simulated decisions for a given random seed are the same as in 0.2.0.
- With it, only a named property is changed, over many items; an empty list and an item with
  no named text are refused; planted errors are judged exactly as without it.
- The note appears in a report with a shortage and not in one without, and both reasons are
  shown to cause a shortage while planted errors are ready.
- The guide, DEP-003, MT-004 and the procedure carry the wording above, and the validation
  pack's checks and traceability matrix pass.
- Databases made by 0.2.0 open without an upgrade, and their logs verify.
- Breaking each new safeguard makes a test fail.
- The use case, with the new package installed, can drop its own workaround for problem 1 and
  still passes its checks.

## How the implementation was checked

- Automated tests cover `reword` and the report note. Two of them show each reason causing a
  shortage while a planted error is ready.
- On 28 November 2026, without `reword`, both built-in profiles and a third that often edits made
  the same 18,000 judgments, times included, in the new code and in release 0.2.0 from PyPI.
- The same day, databases made by 0.2.0 were verified by the new code against a head that 0.2.0
  had exported, then written to without an upgrade, and verified again.
- The same day, a copy of the use case, with a package built from the new code, dropped its
  workaround for problem 1 and used `reword`. It made the same 180 decisions as with 0.2.0 and
  the workaround, passed its own checks and tests, and its report showed the note.
- The guide, DEP-003 and MT-004 carry the wording above. Breaking each of 8 new safeguards in
  turn made a test fail. The comparisons and the wording check were run by hand.
- **Not checked:** the rule on counts with any reviewer or review screen, whether review tools
  can meet it, and whether a QA reader understands the note.

