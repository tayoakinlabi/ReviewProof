# Changelog

ReviewProof uses semantic versions. Before 1.0.0, any release may change the API or the
database format. Each release says what a validated user would need to retest.

## Unreleased

### Added

- Metrics from verified evidence: catch rate with 95% intervals, by defect class, fast
  approvals, review time, agreement, workload, and fatigue through a session.
- A one-page HTML report and a CSV report, with individual results only on request.
- Thresholds, with alerts to the QA lead by email or webhook.
- Commands: `rp init`, `rp system register`, `rp plant`, `rp report` and `rp alerts`.
- A reviewer simulator, and an end-to-end demo with synthetic data.
- Settings: optional `reporting` and `alerts` sections, and per-queue `thresholds`.
- Qualification sessions: planted errors and correct items, a pass mark fixed in advance,
  and each reviewer's status for the current version of the AI system (`rp qualify`).
- For agents that pause and resume: `submit(..., key=...)` is safe to repeat, and
  `settle()`, `call_status()` and `execute_call()` connect a decision to the paused call.
  `reviewproof.adapters.tool.ReviewedTool` wraps a write tool. See
  [ADR-005](docs/adr/0005-adapters.md). Adapters for particular agent libraries are not
  included yet.

### Change impact

**This release needs database schema version 2.** It refuses to write to databases made by
0.1.0 until you run `rp init`, so installing it changes nothing in your records by itself.
**Back up both databases first.** The upgrade rebuilds the log table: stored values and
hashes do not change, and heads exported by 0.1.0 still match. After it, 0.1.0 cannot open
the databases. A validated user should run `rp verify` with a previously exported head
before and after, and retest the gate, guard and log.

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
