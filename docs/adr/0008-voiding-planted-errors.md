# ADR-008: Voiding a faulty planted error

- **Status:** Accepted
- **Date:** 2026-11-27

## Context

A planted error can turn out to be faulty: the item was correct, it had a second defect, the
expected decision was wrong, or no reviewer could have found the defect. Before this record
it could only be retired, which stops further use. **Its past results stayed in every score.** A reviewer who
rightly approved a correct item counts as having missed an error.

The effect can decide a judgement. With the default target of 80%, 8 catches in 11 results are
a breach. If one of the 3 misses was on a faulty planted error, the other results are 8 in 10,
within the limit. In a qualification session one can turn a pass into a fail. The
[planted-error procedure](../../validation-pack/sop-planted-errors.md#10-a-planted-error-that-is-wrong-or-recognisable)
could only note the corrected reading beside each report.

**The fix is more dangerous than the fault.** Anyone who can remove results can remove genuine
misses, and review then looks better than it is. The catch rate is evidence about a safety
control. The rule behind every decision below:

> Voiding must never make results look better without saying so.

## Decision

### What voiding does

Voiding declares that a planted error's results measure nothing.

- It covers **the whole planted error**: every copy, every reviewer, **catches and misses
  alike**, in live review and in qualification sessions, including copies still waiting. One
  result, or one reviewer's results, can never be voided.
- Nothing is scored again. Results are either counted as they were, or left out.
- Nothing is deleted. Every decision stays in the log.
- It is **permanent**, and it also retires the planted error. One already retired can still be
  voided.
- **The guard is unchanged.** A voided planted item is still never written.
- Only a planted error that has been released or put in a session can be voided. Any other is
  simply retired.

### Reasons

One reason from this list, with a written explanation and a reference to a quality record:

| Reason | Meaning |
| --- | --- |
| `not_defective` | The item was correct |
| `more_than_one_defect` | It had another defect, so a right decision could be scored as a miss |
| `wrong_expected_decision` | The decision, or the defect field, that scores as a catch was not what procedures require |
| `not_findable` | What was needed to find the defect was neither on screen nor in any record the reviewer is expected to check |

**Not reasons:** that it was hard, that many reviewers missed it, or that it could be
recognised as planted. A recognisable one is retired, and the procedure records that catches on
it may overstate attention.

### Who can void

One person requests, and a different person confirms, on every queue.

- At least one of the two **neither wrote nor approved** the planted error.
- Neither ID may be one that was shown or decided on a copy, or that sat a session holding it.
- Names are compared as for approvals. A request has no effect until confirmed, and can be
  cancelled.

The database itself also refuses a confirmation by the requester, and one where neither of
the two is independent.

**ReviewProof cannot tell who is typing** (DEP-008). It checks only that the names it is given
differ. Reviewer IDs and staff names may not be of the same kind, so the second rule catches
only a matching ID. Your procedure and signatures must carry these rules.

### How it is recorded

- `planted.db` keeps the request and the confirmation: planted error, reason, explanation,
  reference, both names, both times and a random salt. Its rows cannot be changed or deleted.
- On confirmation, the log gets one `planted_voided` event. It holds **only a commitment**, a
  salted hash of that record and of the planted error, with the queue and the AI system version.
  It names no planted error, item, reviewer or reason, so nothing in `oversight.db` tells which
  waiting item is planted (REQ-012).
- **A void takes effect only when its event is in the log.** A record without an event has no
  effect. If something fails between the two databases, the same person confirms again to
  complete it, or the request is cancelled. Confirming also retires the planted error, so
  after such a failure it stays retired: that only stops further use.
- `rp verify` requires every `planted_voided` event to match exactly one record. Otherwise it
  fails, and no scores are shown. So deleting or editing a void, to bring results back or
  change its reason, is detected. Removing the event breaks the chain. Cutting the log short is
  detected against a head exported earlier.

### What changes in results

| Left out | Still counted |
| --- | --- |
| Catch rate, each kind of defect, catch rates through a session, the catch trend in the agreement check, repeat sightings, individual scores and flags, the 10-planted-error minimum, and every threshold made from these | Workload, fast approvals and review time. The reviewer did that work |

A void applies to every report made after it, whatever period it covers. Reports already made
do not change. Feedback not yet delivered about a voided planted error is not delivered.
Correcting feedback already given is a procedure step.

### Never quietly better

1. **Both figures, always.** Wherever voided results affect a report, it shows for each queue:
   how many planted errors were voided; how many results were left out, **as catches and as
   misses**; the reasons; and the catch rate **with** them beside the catch rate without. CSV
   output has the same. With `--individuals`, each reviewer's result shows both. No planted
   error or explanation is named (REQ-041).
2. **A new threshold check, "Voided results".** It is a breach when either:
   - a catch rate, defect class, agreement or fatigue check would be a breach **with** the
     voided results, but is not without them, **including because too little data is left to
     judge it**; or
   - more than 10% of the report's planted-error results are voided, judged from at least 10.
     The setting is `voided_share_max`. **The 10% is an untried proposal.**

   Like any breach, it appears in the report and is sent to the QA lead by `rp alerts`.

### Reviewer privacy

What was left out, and the figure that includes it, are figures like any other. ADR-004's rule
applies: one that rests on a single reviewer with too few planted errors (not counting voided
ones) is withheld. A "Voided results" check that would need it is shown as withheld, never as
met. Comparing a report made before a void with one made after can still show how the people
who met it decided. The software cannot prevent that.

### Qualification

- A session's items, answers, pass mark and recorded result **do not change**. `rp verify`
  still checks them as recorded.
- A recorded pass still counts **only if the reviewer meets the original `min_caught` counting
  catches on the planted errors that are not voided**. Otherwise their status is
  `requalification required`. False rejections do not change.
- **The mark is never lowered, and a fail never becomes a pass.** A failed session stays
  failed. The reviewer can take a new one.
- `rp qualify status` says when a result rests on a session with a voided planted error, and
  how many planted errors in it still count. That can be fewer than 10.
- A faulty **correct** item in a session is not covered by this record.

For example, with a mark of 9 of 10 and one planted error voided: a reviewer who passed with 10
catches still passes, with 9 catches on the 9 that count. One who passed with 9, including the
voided one, now has 8 and must requalify. One who missed only the voided one keeps the pass.

**A mark that requires every planted error leaves no room.** With a mark of 10 of 10, one void
means that nobody who passed that session can keep the pass, however well they did. The same
happens to a mark of 9 of 10 when two are voided.

### Database and commands

The log accepts only listed event types, so a new type means rebuilding the log table again:
`oversight.db` **schema version 4**, with version 2's check that every row is copied exactly.
`planted.db` **version 3** adds the void records. Earlier versions are not edited. The upgrade
rules of [ADR-006](0006-qualification.md#database-upgrade-schema-version-2) apply.

`rp plant void`, `rp plant void-confirm` and `rp plant void-cancel`, with Python equivalents.
Unlike the other `rp plant` commands that write, the three void commands need both databases.
`rp plant list` reads the log to show voided planted errors, `rp verify` says how many voids are in effect, and the pack's
`planted_records.py` lists each void and who met the planted error.

### What was observed

On 27 November 2026, a current `oversight.db` refused an event of a new type, by its check
constraint. In the project's hand-checked example history, planted errors are retired after
reviewers have decided on them, and the report still counts those results. Nothing else here has
been tried.

## What this would not stop

- Two people acting together, or one person using two names.
- A reason that is wrong or dishonest. The software cannot judge whether a planted error was
  faulty.
- **Voiding selectively:** among several faulty planted errors, voiding only those that were
  mostly missed. The report shows how many catches and misses were left out, and the procedure
  should require every planted error with the same fault to be reviewed together.
- Not voiding a faulty one at all.
- Someone quoting the figure without voided results and leaving out the other.

## Alternatives not chosen

- **Keep retiring, and annotate reports by hand.** Unfair results stay in the figures, in
  alerts and in qualification status, and hand-corrected figures cannot be traced.
- **Change or delete the records.** That destroys the evidence.
- **Change the expected decision and score again.** It would let the scoring rule be chosen
  after the answers are known.
- **Void single results, or one reviewer's.** This is the easiest way to remove inconvenient
  misses.
- **Allow a void to be undone.** Figures could be switched back and forth. A mistaken void
  cannot be reversed, but the figure that includes its results stays visible.
- **Record voids only in `planted.db`.** One could then be added or removed without notice.
- **Name the planted error in the log.** While a copy waits, that could show which item is
  planted.
- **Void automatically** what most reviewers miss. That removes exactly what the tool exists
  to find.

## Consequences

- Another database upgrade that rebuilds the log table. Stored values and hashes would not
  change.
- **In a small program a void may raise no alert.** When the results that show how voided
  ones were decided rest on one reviewer with too few other planted errors, they are withheld,
  and the "Voided results" check is shown as withheld, not breached. This is likely early on.
  The count of results left out is still shown, and the share limit still applies.
- Reports gain a section and a check. A justified void that changes a judgement keeps raising
  that check until its results fall outside the report or alert period.
- **Some reviewers would have to requalify because of a QA error**, when their pass depended on
  the voided item.
- Voiding needs a person who neither wrote nor approved the planted error. With an author and
  two approvers on a GxP queue, a small team may need a fourth person.
- This changes parts of ADR-003, ADR-004 and ADR-006.
- In the validation pack: requirements REQ-034, REQ-036, REQ-039, REQ-040 and REQ-042 change
  and new ones are added; a new risk, **voiding is used to hide genuine misses**, proposed
  High; a new deployment duty to control who voids and to review every void; and the three
  procedures change.

## How the implementation was checked

- Automated tests cover who may void, what a request and an interrupted void do, what the log
  holds, tampering with each part of a void's record, every case of the new check, one-reviewer
  withholding, feedback, qualification, the report, the commands, and the upgrades.
- On 27 November 2026, a database made by release 0.1.0, and one made by the repository's code
  before this record (schema versions 3 and 2), were upgraded. No earlier row changed, and each
  log verified against the head the earlier code had exported. In the second, a planted error
  from its history was then voided with the commands. Confirmations by the requester, by an
  approver with a requester who had also approved, and by a reviewer were refused, and the log
  entry held only a commitment. A recorded pass of 12 of 12, with a mark of 10, still counted
  after two of its planted errors were voided, and stopped counting after the third. This was
  run by hand.
- Breaking each of 46 safeguards in turn made a test fail.
- Not checked: voiding on a real review queue, older SQLite versions and large databases.
