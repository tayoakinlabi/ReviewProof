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

`rp plant` and `rp system` act on planted errors or settings. Run them where reviewers cannot
see.

## `rp init`

Create the two databases, once. It refuses to create a new file when only the other exists:
restore both from the same backup.

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

## `rp plant list`

List planted errors, their status and any that are held back. It never shows their content.

Options: `--config`, `--planted`, `--queue`, `--status`.

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

Check that the log has not been changed. With `--planted` it also checks the item records.
The databases are opened read-only.

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

A group score that rests on one reviewer and fewer planted errors than
`individual_min_planted` is withheld, because it would be that person's score. Someone who
has a group score and all other members' scores could still work out a hidden one by
subtraction.
