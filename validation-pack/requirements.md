# Requirements specification

**Template for validating ReviewProof.** Read the [overview](README.md) first.

| | |
| --- | --- |
| Software | ReviewProof, version **[FILL: version you are validating]** |
| Document owner | **[FILL]** |
| Approved by, and date | **[FILL]** |

There are two lists:

- **Requirements (`REQ-`)** are what the software itself must do.
- **Deployment duties (`DEP-`)** are what your application, infrastructure and procedures must
  do. ReviewProof cannot do or check them. **Its own tests say nothing about them.**

The [traceability matrix](trace-matrix.md) shows which automated tests are tagged with each
requirement, and the [test scripts](test-scripts.md) say how to run them and how to test the
duties.

The terms used here: an **item** is one AI proposal under review. A **planted error** is an item
with a known defect, written by your staff. The **guard** is the check that every write goes
through. The **restricted database** is `planted.db`.

IDs are permanent. Add new ones at the end, and mark a withdrawn one as withdrawn instead of
reusing its number. Mark any requirement that does not apply to your intended use, and say why.

## Requirements

### Planted-error library

| ID | The software shall |
| --- | --- |
| REQ-001 | Import planted errors from files its users write, and refuse one whose content does not fit the queue's item schema or whose defect field is not a property of that schema. |
| REQ-002 | Give each planted error its own target ID in a reserved range, refuse a second import of the same content, and refuse to change a planted error by importing its ID again. |
| REQ-003 | Use a planted error only after approval by someone other than its author: by two different people on a queue marked GxP, by one otherwise. Names are compared ignoring case and surrounding spaces. |
| REQ-004 | Refuse changes to a planted error's content after import. Record approvals and retirement once each, with who did it, and refuse deletion. |
| REQ-005 | Never create a planted error from an item submitted for review. Optional drafting helpers work only from an item the user supplies. |

### Releasing planted errors

| ID | The software shall |
| --- | --- |
| REQ-006 | With each real item submitted, release a planted error with probability p / (1 − p) for the queue's planted share p, each draw independent of the others, using the operating system's secure random source unless the host's code supplies another. |
| REQ-007 | Release only approved, unretired planted errors for live use that still fit the item schema; never one with a copy awaiting a decision; never one the named reviewer has been given, shown or has decided on; and choose among the least used. |
| REQ-008 | When a planted error is due and none can be used, release none, record the shortfall and not make it up later. |
| REQ-009 | Accept a real item even if the planted-error side fails or the item does not match its schema, and record a warning or error that names no planted error. If either database is unavailable, refuse the submission. |

### Concealment

| ID | The software shall |
| --- | --- |
| REQ-010 | Return to the host, for each item, only an opaque token and the item's content: no marker of its kind and no target ID. |
| REQ-011 | Treat real and planted items alike in everything the review application sees: token form, field order, the random order of a pair, and the same refusals with the same messages. |
| REQ-012 | Before an item is decided, show nothing in `oversight.db`, in its errors or in its log messages that tells whether the item is planted. Its logs and errors shall not name a planted error. |

### Execution guard

| ID | The software shall |
| --- | --- |
| REQ-013 | Never pass a planted item to the host's write function, whatever was decided about it. |
| REQ-014 | Call the write function only when all of these hold: the item has one decision, approve or edit; the restricted database says it is real and agrees with what the log committed to at submission; the content is exactly what was approved or recorded as the edit; the target ID is not in the reserved range; and execution has not been requested before. |
| REQ-015 | Execute nothing when any of those checks fails or cannot be completed, including when a database is unavailable or damaged. |
| REQ-016 | Call the write function at most once for an item, including under simultaneous requests and after a crash or a failed write, and never retry it. |
| REQ-017 | Not use up an item's one attempt when a request is refused before execution is requested, for example during an outage. |
| REQ-018 | Report with a distinct error when the write function ran but its result could not be recorded. |
| REQ-019 | Refuse a real item whose target ID is in the reserved range, or whose content contains its target ID. |

### Recording reviews

| ID | The software shall |
| --- | --- |
| REQ-020 | Record exactly one decision for an item, only from a reviewer for whom it has recorded that the item was shown, identified by the ID the host supplies. |
| REQ-021 | For an edit, work out itself which top-level fields changed, and refuse an edit that changes nothing. |
| REQ-022 | Record each showing of an item and, when the host supplies it, the names of what was on screen, without their content. |
| REQ-023 | For real items submitted for review, store no item content and no token, only salted hashes. (Planted errors and qualification items are stored in full in the restricted database.) |

### Evidence

| ID | The software shall |
| --- | --- |
| REQ-024 | Add every event to a hash-chained log that its database refuses to update, delete from or replace in. |
| REQ-025 | Record for each event its type, UTC time, AI system version, and, where they apply, queue, item, reviewer and decision. |
| REQ-026 | On verification, report any changed, removed or reordered event; and, given a head exported earlier, also a log whose latest events were removed or that was rebuilt. |
| REQ-027 | Commit in the log, for each item, to whether it is real or planted, its target ID and any planted error behind it with its approvals, and report on verification any later change to them. |
| REQ-028 | Verify without changing either database. |
| REQ-029 | Stop simultaneous writers from splitting the chain. An operation that cannot get the write lock within its time limit shall be refused and add nothing to the log. |

### AI system versions and reviewer qualification

| ID | The software shall |
| --- | --- |
| REQ-030 | Tie each item to the AI system version in force when it was submitted (model, model version, prompt version, tool schema), record each change, including a return to an earlier version, and flag the affected reviewers for requalification. |
| REQ-031 | Record a qualification session's pass criteria before it starts, require at least 10 planted errors and 5 correct items, and refuse criteria that cannot fail a reviewer who answers every item the same way. |
| REQ-032 | Keep qualification planted errors separate from live ones, never give a reviewer one from an earlier session of theirs, and never execute a session item or count it in live metrics. |
| REQ-033 | On verification with the restricted database, check that session items, answers and results are unchanged, and work each result out again. |
| REQ-034 | Report a reviewer as qualified for a queue only if they passed a session that started after the queue's latest change of AI system version. (It reports this and does not stop anyone reviewing.) |

### Metrics, reports and alerts

The definition of every figure is in the [command reference](../docs/cli.md#how-the-figures-are-defined).

| ID | The software shall |
| --- | --- |
| REQ-035 | Show scores only when the evidence verifies. Otherwise show none and say why. |
| REQ-036 | Work out catch rate as planted errors caught out of planted errors decided at the decider's first sight, leaving undecided ones out and reporting repeats separately, with a 95% Wilson interval. |
| REQ-037 | Score a planted error as caught only if it is rejected, or edited in its defect field. |
| REQ-038 | Work out fast approvals, review time, agreement, workload and fatigue as the command reference defines them. |
| REQ-039 | Judge each threshold as breached, met or not enough data, and never present not enough data as met. |
| REQ-040 | Show individual results only when asked, and only from at least 10 first-sight planted errors, a minimum settings can raise but not lower; withhold a group figure that rests on one reviewer with fewer; and send alerts only to the QA lead, with queue-level findings only. |
| REQ-041 | Name no item and no planted error in a report. |
| REQ-042 | Give a reviewer feedback only on planted errors they decided, once each, at the time the queue's setting allows. |

### Network, settings and databases

| ID | The software shall |
| --- | --- |
| REQ-043 | Make no network connection except to send alerts, to the routes in its settings, when asked to send them. It shall send no usage data. |
| REQ-044 | Refuse invalid settings and say what is wrong, including a reserved target prefix too weak to protect. |
| REQ-045 | Never create a database just by opening one, and never create a new database beside an existing one of the pair. |
| REQ-046 | Change a database's schema only when asked to upgrade it, copy the log exactly or undo the whole upgrade, and refuse a database made by a newer version. |

### Agents that pause, and adapters

Apply these only if you use them. An adapter meets them **only under the conditions in its
guide**: [LangGraph](../docs/guides/langgraph.md),
[OpenAI Agents SDK](../docs/guides/openai-agents.md),
[Pydantic AI](../docs/guides/pydantic-ai.md), [Strands Agents](../docs/guides/strands.md),
[Microsoft Agent Framework](../docs/guides/agent-framework.md).

| ID | The software shall |
| --- | --- |
| REQ-047 | When a submission is repeated with the same key, target, content and reviewer, return the same tickets, record nothing and not draw again; and refuse the key for anything else. |
| REQ-048 | Run a paused call's write only through the guard, so that no recorded approval or edit of that content means no write, and so that a decision on a planted item releases nothing. |
| REQ-049 | Through each adapter, put no ticket token and no planted content into the agent's messages or saved state. |
| REQ-050 | Through each adapter, write nothing because of what the agent library says: not on a forced or repeated resume, a standing approval or a replayed pause. |
| REQ-051 | Through each adapter, end the agent's run when an approved write fails, without giving the model the chance to try it again. |
| REQ-052 | Through each adapter, run each of several calls proposed at once no more than once. |

### Default planted share

| ID | The software shall |
| --- | --- |
| REQ-053 | Where a queue's settings give no planted share, use 1%, 2% or 3% according to the queue's VITAL rating, as [ADR-007](../docs/adr/0007-vital-ratings.md#default-planted-share) sets out; where they give one, use it. |

## Deployment duties

For each duty, record how you meet it and how you tested it. Where the tool's design records
explain why, see the [design records](../docs/adr/) and the
[integration guide](../docs/guides/integration.md).

| ID | You shall | How you meet it, and evidence |
| --- | --- | --- |
| DEP-001 | Make every write of a reviewed item go through ReviewProof's guard, and give no agent or code path your raw write function. **Test this in your own environment: a planted error approved on your review screen never reaches your target system.** | **[FILL]** |
| DEP-002 | Make your write function or target system reject target IDs in the reserved range. This guards against a fault in ReviewProof, not against code that goes round it. | **[FILL]** |
| DEP-003 | Queue every ticket, and never show reviewers which tickets arrived together, when each arrived, or the order they arrived in. | **[FILL]** |
| DEP-004 | Show a reviewer nothing about an item except what its ticket holds. If your screen must show more, such as sources or history, do not use planted errors on that queue. | **[FILL]** |
| DEP-005 | Keep reviewers from watching the agent's traces, saved state and logs, the target system as it changes, and whether the agent continues after a decision, including through when new items arrive, while items are waiting. | **[FILL]** |
| DEP-006 | Show reviewers nothing different when the guard blocks an item, and keep server-side status results away from them. | **[FILL]** |
| DEP-007 | Limit access to the database files. Reviewers shall have none. Only the application and QA administrators shall be able to write the restricted one. Run administration commands where reviewers cannot see them. | **[FILL]** |
| DEP-008 | Authenticate reviewers, authors and approvers, pass ReviewProof a stable pseudonymous ID for each, and keep the list that links IDs to people. **ReviewProof does not check who anyone is.** | **[FILL]** |
| DEP-009 | Control the clock of every machine that runs ReviewProof. | **[FILL]** |
| DEP-010 | Export the log's head at set intervals to storage that ReviewProof and its administrators cannot rewrite, and verify against it. Without this, a rebuilt log cannot be detected. | **[FILL]** |
| DEP-011 | Back up and restore the two databases as a pair, test the restore, and back both up before any upgrade. | **[FILL]** |
| DEP-012 | Have qualified staff write and approve realistic planted errors, from synthetic or de-identified data, with a real defect in each. Keep enough of them, replace ones reviewers may know, and act on recorded shortfalls. | **[FILL]** |
| DEP-013 | Tell reviewers about the program, limit who sees individual results and for what purpose, and complete the privacy, employment and works-council reviews that apply to you. | **[FILL]** |
| DEP-014 | Decide in your procedures who may review without a current qualification, and enforce it. | **[FILL]** |
| DEP-015 | Keep the settings file under change control. Thresholds apply to past decisions when a report is made, and the file's AI system version replaces the recorded one. | **[FILL]** |
| DEP-016 | Handle ReviewProof's errors as the guides describe. After a write that failed or could not be recorded, have a person check the target system. With an adapter, meet every condition in its guide. | **[FILL]** |
| DEP-017 | Decide what your agent library, model provider and tracing tools may send and store, and set them accordingly. Some send tracing data by default. | **[FILL]** |
| DEP-018 | Fix the versions of ReviewProof and of your agent library, test them with data representative of your work before use, and test again before changing either. | **[FILL]** |
