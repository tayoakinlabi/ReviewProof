# ADR-006: Reviewer qualification sessions

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

A reviewer should be qualified for an AI system and task before reviewing its live work, and
again after it changes. A qualification result is evidence, and also a measurement of one
person. It must be fair, fixed in advance, and impossible to change without notice.

## Decision

### What a session holds

- At least **10 planted errors** (the queue's `individual_min_planted`), because a result is an
  individual score.
- At least **5 correct items**. With only defective items, a reviewer who rejected everything
  would pass.
- A pass mark set as counts: `min_caught` (from 1 to the number of defects) and
  `max_false_rejects` (from 0 to **at most half** of the correct items). A correct item counts
  as wrongly rejected if it is rejected **or edited**.
- A defect is caught under the same rule as live work (ADR-003).

### A separate pool

Planted errors have a `purpose`, `live` (the default) or `qualification`, fixed at import. The
gate uses only live ones and sessions use only qualification ones, because what a reviewer
learns in a session must not turn up in their live queue. A reviewer is never given a planted
error from one of their earlier sessions, including abandoned ones. Correct items may be
reused, which is a smaller risk but a real one: rotate them too.

### Fixed in advance, and recomputed

`qualification_started` holds the pass mark and, for each item, a salted commitment to its
payload, its position, which planted error it is (if any) and that planted error's fingerprint.
Items are in random order, and nothing in the log or the reviewer's tickets shows which are
defective. Scoring reads the pass mark from the log. Before scoring, the stored items are
checked against their commitments. `rp verify --planted` repeats that check and **works every
result out again** from the logged answers. The `qualifications` table is a summary; nothing
relies on it.

### Sessions

One unfinished session per reviewer and queue. Every item gets one answer. A session can be
finished only when all are answered, or abandoned, which counts as not passed. Nothing in a
session reaches an executor, the live metrics or the live gate. A reviewer's own judgment can be
recorded **before** an item is shown (`judged`); it is stored in the restricted database, with a
hash in the log.

### Who is qualified

Worked out from the log: a reviewer is `qualified` for a queue if they passed a session that
**started** after the queue's latest change of AI system version, under the current version.
If they passed only before that, requalification is required. Going back to an earlier version
is also a change. ReviewProof reports this; **it does not stop anyone reviewing.** The host
decides what an unqualified reviewer may do.

### Database upgrade (schema version 2)

New log entry types need the `events` table to be rebuilt, because SQLite cannot widen a check
constraint. The upgrade copies every row, checks that none differs, and only then replaces the
table, in one transaction that is undone if anything fails. Stored values and hashes do not
change, so a head exported by 0.1.0 still matches. This was tested on a database created by the
released 0.1.0 package. `planted.db` gains `purpose` and two tables.

**An upgrade runs only when someone asks for it:** `rp init`, or `initialise(...,
upgrade=True)`. Anything else that would write refuses a version 1 database, says so and
changes nothing, so installing a new release cannot by itself alter regulated records.
Commands that only read (`rp verify`, `rp report`) work on a version 1 database without
changing it. Once upgraded, 0.1.0 refuses the database. This rule applies to later schema
versions too.

## Consequences

- QA has a second set of planted errors to write, and needs new ones for each retake.
- Sessions are offline: there is no dwell time, and `shown` is optional.
- A reviewer could learn from a colleague who sat the same session first. Use different items
  for people who can talk to each other.
- The `qualification_status` column on `reviewers` is one value per person and is only a
  summary. Use `status()`, which is per queue and version.
- Installing a release with a new schema takes one more step: back up both databases and run
  `rp init`. Until then the application cannot start.
