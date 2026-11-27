# ADR-004: Metrics, thresholds and reports

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

Reports are evidence a Quality Unit may show an inspector, and they include measurements of
individual people. Several definitions decide what a number means, and whether a person is
treated fairly. The thresholds are proposed values, to be calibrated in a pilot.

## Decision

### Evidence first

Metrics come from the event log, plus the restricted database for which items are planted, and
only after `verify` passes, including the registry commitments (ADR-002). Verification and
reading happen in one read transaction. If verification fails, there are **no scores**: the
report says why, and `rp alerts` alerts about the failure.

### What counts

- **Catch rate:** planted errors caught, out of planted errors **decided at the decider's first
  sight** of that planted error. First sight is the first time that reviewer was shown any item
  made from it. Later sights are repeats, reported separately. A planted error shown and not yet
  decided is pending, **not a miss**.
- A catch is scored as in ADR-003, whether or not the host ever asked to execute the item.
- **Dwell time:** from the decider's first showing of the item to their decision. A screen left
  open, or reloaded, lengthens it.
- **Fast approvals:** approvals under the queue's minimum review time, over all approvals,
  planted items included.
- **Agreement:** **real** items approved unchanged, over real items decided. Planted errors are
  not the AI's proposals.
- **Sessions:** a reviewer's decisions, across queues, with no gap over the queue's session gap
  (30 minutes). Hours of a session stand in for hours of a shift, which ReviewProof does not
  know. Days and ISO weeks are UTC.
- **Period:** a decided item belongs to the period it was decided in.
- Every rate carries a 95% Wilson interval.

### Thresholds

Each rule is **breach**, **ok** or **not enough data**, and the last is never shown as a pass.

| Rule | Breach when | Judged from |
| --- | --- | --- |
| Catch rate | Below 80% over the latest 50 planted errors | 10 planted errors |
| Defect class | Any class below 60% | 10 planted errors in that class |
| Fast approvals | Over 20% in any ISO week | 10 approvals in that week |
| Agreement | Over 98% and the later half of planted errors caught less than the earlier half | 20 planted errors and 10 real items |
| Fatigue | Catch rate 20 points or more lower in the second half of sessions | 10 planted errors in each half |
| Load | A reviewer decides over the daily cap (only if a cap is set) | |
| Safety | The guard stopped a reserved target ID, or could not complete a check | |

The sample sizes for a class or a week, the meanings of "rolling" and "falling", and the
definition of a session are choices made in this record.

### Privacy

- Reports are about the system unless individual results are asked for.
- An individual's results are shown only from **10 first-sight planted errors**, a minimum that
  settings can raise but not lower. Below it, only their workload is shown.
- Any group figure resting on one reviewer with fewer than that many is withheld, including its
  threshold checks.
- Alerts hold queue-level findings only. Their only recipient type is the QA lead.
- A catch rate below target is worded by its interval: **clearly below** only when the whole
  95% interval is below the target, otherwise **could be chance**.

### Where figures come from

Planted errors ready or held back, and shortfalls, come from the restricted database and the
settings, not the verified log, and the report says so.

## Consequences

- **Chance matters for individuals.** With 10 planted errors, a reviewer who truly catches 90%
  falls below an 80% point estimate about 7% of the time, and one who truly catches 85% about
  18%. That is why the wording depends on the interval, and why a flag is a prompt to look.
- With several rules checked on one queue, the chance that at least one is breached by luck is
  higher than for any single rule.
- Thresholds are judged on whatever the period holds. Changing the period can change a result.
- Subtraction can reveal a hidden score to someone who holds a group score and every other
  member's score. Individual reports are for QA only.
- `min_review_seconds` and the thresholds are read from the settings at the time of the report,
  so changing them changes how past decisions are judged. Each report lists the limits used.
- Each run of `rp alerts --send` reports what is outside its limits then; a lasting breach is
  sent again.
