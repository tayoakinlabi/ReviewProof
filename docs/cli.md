# The `rp` command

For engineers and QA administrators. Run `rp COMMAND --help` for the same details at the
terminal.

## Files and exit statuses

Unless told otherwise, commands read `reviewproof.yaml`, `oversight.db` and `planted.db` in
the current folder.

| Exit status | Meaning |
| --- | --- |
| 0 | Done |
| 1 | The evidence did not verify |
| 2 | The command was refused or could not run |
| 3 | `rp alerts --send` could not deliver an alert |

`rp plant`, `rp qualify` and `rp system` act on planted errors or settings. Run them where reviewers cannot
see.

## `rp init`

Create the two databases, once. It refuses to create a new file when only the other exists:
restore both from the same backup.

It also upgrades databases made by an earlier release, and says which it upgraded. Nothing
else does: other commands that write refuse an old database, while `rp verify` and
`rp report` read it without changing it. **Back up both databases first.** Earlier releases
cannot open an upgraded database.

Options: `--db`, `--planted`.

## `rp system register`

Record a new version of the AI system behind a queue and list the reviewers who now need
requalification. **Update the settings file too**: the file wins the next time ReviewProof
opens.

Options: `--config`, `--db`, `--planted`, `--queue`, `--model-id`, `--model-version`,
`--prompt-version`, `--tool-schema-hash`.

## `rp plant import`

Check one or more planted-error files and store the good ones, not yet approved. See the
[authoring guide](guides/writing-planted-errors.md).

Options: `--config`, `--planted`.

```bash
rp plant import planted/dose_swap.yaml planted/missing_action.yaml
```

## `rp plant approve`

Record one approval. ReviewProof cannot verify who is typing: control who can write to
`planted.db`.

Options: `--config`, `--planted`, `--approver`.

```bash
rp plant approve PE-0012 --approver qa-lead-1
```

## `rp plant retire`

Stop using a planted error, permanently.

Options: `--config`, `--planted`, `--by`, `--reason`.

Retiring leaves the planted error's past results in every score. If it was faulty, void it.

## `rp plant void`

Ask to void a **faulty** planted error ([ADR-008](adr/0008-voiding-planted-errors.md)).
**Nothing changes** until a different person confirms. It must have been released or put in a
session. `--reason` is one of:

| Reason | Meaning |
| --- | --- |
| `not_defective` | The item was correct |
| `more_than_one_defect` | It had another defect, so a right decision could be scored as a miss |
| `wrong_expected_decision` | The decision, or the defect field, that scores as a catch was not what procedures require |
| `not_findable` | What was needed to find the defect was neither on screen nor in any record the reviewer is expected to check |

That it was hard, widely missed or recognisable is not a reason.

Options: `--config`, `--db`, `--planted`, `--by`, `--reason`, `--explanation`,
`--reference` (your quality record).

```bash
rp plant void PE-0012 --by qa-lead-1 --reason not_defective --explanation "The dose was right" --reference DEV-0412
```

## `rp plant void-confirm`

Confirm someone else's request. **All the planted error's results then leave every score, catches
and misses alike, and this cannot be undone.** It is also retired. At least one of the two people
must be someone who neither wrote nor approved it, and neither may be an ID recorded as a
reviewer who was shown it, decided on it or sat a session holding it. **ReviewProof cannot
verify who is typing.** If it fails partway, the same person runs it again.

Options: `--config`, `--db`, `--planted`, `--by`.

```bash
rp plant void-confirm PE-0012 --by qa-head
```

## `rp plant void-cancel`

Drop a request that is not in effect.

Options: `--config`, `--db`, `--planted`, `--by`.

## `rp plant list`

List planted errors, their status, any that are held back, and any void or request for one.
It never shows their content.

Options: `--config`, `--db` (to see which voids are in effect), `--planted`, `--queue`,
`--status`.

## `rp plant draft`

Draft a file from a **correct** item you supply, which must be synthetic or de-identified.
Review and edit the file before importing it.

Options: `--config`, `--queue`, `--from`, `--item`, `--mutator` (`value-swap`, `omission` or
`classification-flip`), `--field`, `--new-value`, `--remove-index`, `--to`, `--id`,
`--author`, `--out`.

```bash
rp plant draft --queue deviation-triage --from items.json --item 2 \
  --mutator classification-flip --field classification --id PE-0013 --author me \
  --out PE-0013.yaml
```

## `rp qualify start`

Start a qualification session and write the reviewer's worksheet, which says nothing about
which items are defective. See [qualifying reviewers](guides/qualification.md).

Options: `--config`, `--db`, `--planted`, `--queue`, `--reviewer`, `--plan`, `--out` (never overwritten).

## `rp qualify finish`

Record the answers from a file, then score the session against the pass mark fixed at its
start. A corrected file can be run again: answers already recorded are not changed.

Options: `--config`, `--db`, `--planted`, `--queue`, `--answers`.

## `rp qualify abandon`

End a session early. It counts as not passed, and its planted errors count as used.

Options: `--config`, `--db`, `--planted`, `--queue`.

## `rp qualify status`

Show whether a reviewer is qualified for the queue's current version of the AI system. If a
planted error in their session has been voided, it says how many of the rest they caught,
and whether the pass still counts.

Options: `--config`, `--db`, `--planted`, `--queue`, `--reviewer`.

## `rp report`

Verify the evidence, then make the report for QA: one page of HTML, or CSV. If the evidence
does not verify, no scores are shown, and an HTML report says why.

Options: `--config`, `--db`, `--planted`, `--queue` (repeatable), `--since` (`30d`, `12h`,
`4w` or a date), `--expect-head`, `--format` (`html` or `csv`), `--out`, `--individuals`.

`--individuals` adds results for each reviewer. Use it only for QA, under a written policy.

```bash
rp report --since 30d --out report.html
```

## `rp alerts`

Check each queue against its thresholds. Without `--send` it only prints. With `--send` it
sends one message for each queue that has something outside its limits to the QA lead set in
the settings. If the evidence does not verify, it alerts about that instead.

Options: `--config`, `--db`, `--planted`, `--queue`, `--since`, `--expect-head`, `--send`.

Each run reports whatever is outside its limits **at that moment**, so a problem that lasts is
sent again on the next run. Choose the schedule with that in mind, for example weekly.

## `rp verify`

Check that the log has not been changed. With `--planted` it also checks the item records
and every void, and says how many voids are in effect. The databases are opened read-only.

Options: `--db`, `--planted`, `--expect-head`, `--export-head` (never overwrites a file).

```bash
rp verify --planted planted.db --export-head head-2026-11-26.json
```

# Settings for reports and alerts

Both sections are optional. These are the default thresholds, proposed values to be
calibrated in a pilot. A queue can override any of them under its own `thresholds:` key.

```yaml
reporting:
  catch_rate_min: 0.80            # over the latest rolling_window planted errors
  class_catch_rate_min: 0.60      # each kind of defect
  fast_approval_share_max: 0.20   # in any one week
  agreement_rate_max: 0.98        # alerts only while the catch rate is falling
  fatigue_drop_max: 0.20          # second half of sessions against the first
  voided_share_max: 0.10          # share of planted-error results voided
  min_sample: 10                  # fewest examples before a rule is judged
  rolling_window: 50
  session_gap_minutes: 30
  individual_min_planted: 10      # can be raised, never lowered
  max_items_per_reviewer_per_day: null   # set a number to be warned above it

alerts:
  qa_lead:                        # the only kind of recipient there is
    email: [qa-lead@example.com]
    webhook: https://hooks.example.com/your-secret-address
  smtp:                           # needed for email
    host: smtp.example.com
    sender: reviewproof@example.com
    port: 587
    starttls: true
    username_env: RP_SMTP_USER    # names of environment variables, never the values
    password_env: RP_SMTP_PASSWORD
```

An alert holds queue-level findings only: no reviewer, item or planted error. ReviewProof
makes no network call unless you set a route and run `rp alerts --send`.

## How the figures are defined

| Figure | Definition |
| --- | --- |
| Catch rate | Planted errors rejected, or edited in the defect field, out of planted errors decided at **first sight** by that reviewer. Items still undecided are not misses. |
| Fast approvals | Approvals under the queue's `min_review_seconds`, out of all approvals. Planted items count. |
| Dwell time | From the first time the decider's screen showed the item to their decision. |
| Agreement | **Real** items approved unchanged, out of real items decided. |
| Session | A reviewer's decisions with no gap over `session_gap_minutes`, across queues. It stands in for a shift. |
| Early and late | The first and second half of each session. The middle item of an odd number is early. |
| Day and week | UTC calendar days and ISO weeks. |
| Range | The 95% Wilson interval. |

## Voided planted errors

A [voided](#rp-plant-void) planted error's results are left out of the catch rate, each kind of
defect, catch rates through a session, the catch trend in the agreement check, repeat
sightings, individual scores and flags, the minimum of 10, and every threshold made from these.
They still count in workload, fast approvals and review time. This applies to every report made
afterwards, whatever its period. Feedback not yet given about it is not given.

**Voiding must never make results look better without saying so.** Wherever voided results
affect a queue, a report shows how many planted errors and results were left out, as catches
and misses, the reasons, and the catch rate with them beside the one without. Group tables and
individual results show both. A **"Voided results"** check is a breach, and so reaches the QA
lead, when:

- a catch rate, kind of error, agreement or fatigue check would be a breach with the voided
  results but is not without them, **including because too little is left to judge it**; or
- more than `voided_share_max` of the planted-error results are voided, judged from
  `min_sample`.

**A small program may get no warning.** The figures that show how voided results were decided
are figures about people. When they rest on one reviewer with too few other planted errors,
which is likely early on, they are withheld, and the check is shown as withheld rather than as
a breach. The count of results left out is still shown, and the share limit still applies.

**One person's figures.** A queue's, version's or site's figure that rests on one reviewer
with fewer than `individual_min_planted` planted errors in the report is that person's figure,
so it is withheld: the catch rate, fast approvals, review time and agreement, and each part of
them, such as one kind of error, one week or one position in a session. A threshold check
that would need such a figure is not judged, so it raises no alert and is not shown as met.
Counts of work done, such as items a day, are still shown, because load explains results.

This does not stop **subtraction**. A hidden figure can sometimes be worked out from a total
and its other parts, or from a group's figure and all other members' figures. Limit who sees
reports as well.
