# Changelog

ReviewProof uses semantic versions. Before 1.0.0, any release may change the API or the
database format. Each release says what a validated user would need to retest.

## Unreleased

### Fixed

These concern reporting code that no release contains yet.

- A report on a queue, version or site whose figures rested on one reviewer with too few
  planted errors withheld their scores, but still showed their share of fast approvals,
  review time and agreement. With no planted errors at all it withheld nothing, and an alert
  about fast approvals could describe that one person. All figures about how someone
  reviewed, each part of them, and the checks made from them are now withheld. If you made
  reports from the source before this change, check who received them.

### Added

- Metrics from verified evidence: catch rate with 95% intervals, by defect class, fast
  approvals, review time, agreement, workload, and fatigue through a session.
- A one-page HTML report and a CSV report, with individual results only on request.
- Thresholds, with alerts to the QA lead by email or webhook.
- Commands: `rp init`, `rp system register`, `rp plant`, `rp report` and `rp alerts`.
- A reviewer simulator, and an end-to-end demo with synthetic data.
- First part of a [validation pack](validation-pack/README.md) to adapt: intended-use and
  risk assessment templates, numbered requirements, test scripts for your own deployment, and
  a traceability matrix generated from tagged tests, with a script that turns a test run into
  a record.
- Procedure templates in the validation pack for the planted-error program, reviewer
  qualification, and metric review with reviewer privacy rules; and
  `validation-pack/planted_records.py`, which lists who wrote, approved and retired each
  planted error, and who met a given one.
- In the validation pack, a 21 CFR Part 11 control table and a mapping to the VITAL
  framework's evidence categories. Both say what ReviewProof does not provide.
- Settings take all of VITAL's risk ratings: half steps such as `A5.5`, and the adjusted
  categories `moderate` (the same as `medium`), `very_high` and `critical`. Very High and
  Critical take the top default planted share. No rating from A1 to A10 that 0.1.0 accepted
  gets a different share. See [ADR-007](docs/adr/0007-vital-ratings.md).
- Voiding a faulty planted error, which leaves all its results out of scores, catches and
  misses alike: `rp plant void`, `void-confirm` and `void-cancel`. It takes two people, is
  permanent, and is recorded in the log. Reports show what was left out beside the figures,
  a "Voided results" check flags a void that hides a breach or too large a share (setting
  `voided_share_max`), and a qualification pass counts only while its mark is met without
  voided planted errors. See [ADR-008](docs/adr/0008-voiding-planted-errors.md).
- Settings: optional `reporting` and `alerts` sections, and per-queue `thresholds`.
- Qualification sessions: planted errors and correct items, a pass mark fixed in advance,
  and each reviewer's status for the current version of the AI system (`rp qualify`).
- For agents that pause and resume: `submit(..., key=...)` is safe to repeat, and
  `settle()`, `call_status()` and `execute_call()` connect a decision to the paused call.
  `reviewproof.adapters.tool.ReviewedTool` wraps a write tool. See
  [ADR-005](docs/adr/0005-adapters.md).
- LangGraph adapter, as the optional extra `reviewproof[langgraph]`: `ReviewStep` gives each
  reviewed tool call its own step. See the [guide](docs/guides/langgraph.md).
- OpenAI Agents SDK adapter, as `reviewproof[openai-agents]`: `ReviewTools` builds the
  reviewed function tools. See the [guide](docs/guides/openai-agents.md), starting with
  what the SDK sends as traces by default.
- Pydantic AI adapter, as `reviewproof[pydantic-ai]`, for Python 3.11 or later. **A failed
  reviewed tool raises `RunStopped`, which `except Exception` does not catch.** See the
  [guide](docs/guides/pydantic-ai.md).
- Strands Agents adapter, as `reviewproof[strands]`. **`ReviewTools` must be passed in
  `Agent(hooks=[...])`,** and background tasks must be off. See the
  [guide](docs/guides/strands.md).
- Microsoft Agent Framework adapter, as `reviewproof[agent-framework]`, for Python 3.11 or
  later. **Runs need a session, and a failed reviewed tool raises `RunStopped`, which
  `except Exception` does not catch.** See the [guide](docs/guides/agent-framework.md).

### Change impact

**This release needs new database schemas: version 4 of `oversight.db` and version 3 of
`planted.db`.** It refuses to write to databases made by 0.1.0 until you run `rp init`, so
installing it changes nothing in your records by itself. **Back up both databases first.** If
you have upgraded a database with this repository's source before, you may need to run
`rp init` again. The upgrade rebuilds the log table, replaces the column that holds a
queue's risk category, keeping the old one unused, and adds the void records. Stored values and hashes do not change, and
heads exported by 0.1.0 still match. After it, 0.1.0 cannot open the databases. A validated
user should run `rp verify` with a previously exported head before and after, retest the gate,
guard and log, and check each queue's planted share.

**A `vital_base_risk` above `A10`, such as `A11` or `A55`, is now refused.** 0.1.0 accepted
it and gave it the top share. Correct such settings before installing.

The new figures, reports and qualification results need testing against their intended
use. See [ADR-004](docs/adr/0004-metrics-and-reports.md) and
[ADR-006](docs/adr/0006-qualification.md).

## 0.1.0 (2026-11-26)

The first release with working code: the safety core. **Pre-release: not for production use.**

### Added

- `Oversight` with `submit`, `shown`, `decided`, `execute` and `feedback`, and `initialise` to
  create the two databases.
- A gate that mixes approved planted errors into a queue at random, by risk tier.
- An execution guard that blocks planted errors and runs nothing when a check fails.
- A hash-chained, append-only event log, and `rp verify`.
- A planted-error library with YAML and JSON import, QA approval, an authoring guide and
  drafting helpers.
- Step-trace and review-context capture, and requalification flags when the AI system changes.
- Adapters for a plain approval function and a proposal function.

### Not included

- Metrics, reports, alerts, qualification sessions, a LangGraph adapter and the validation pack.

### Change impact

0.0.0 was a placeholder with no working code, so there is nothing to retest or migrate. Anyone
evaluating 0.1.0 should run the environment test in the
[integration guide](docs/guides/integration.md#test-it-in-your-own-environment) against their
own review screen and target system.

Databases created from earlier unreleased copies of the source code are not supported: create
new ones.

## 0.0.0 (2026-11-26)

Placeholder that reserved the package name.
