# VITAL evidence categories: what ReviewProof contributes

**Template.** For the people validating an AI agent who use the VITAL framework. Read the
[overview](README.md) first. Text marked **[FILL]** is yours to write.

VITAL is described in
[VITAL: A Framework for Validating Agentic AI in Pharmaceutical Drug Development and Clinical Trial Operations](https://arxiv.org/abs/2611.22973v1)
by Burugupalli and others. This page refers to **version 1**, submitted to arXiv on
17 November 2026. It is a **preprint: not a regulation or a standard.** The paper calls its
risk multipliers illustrative, and says the framework should be evaluated further. ReviewProof
is not affiliated with it or endorsed by its authors. The category names below are the
paper's; the descriptions of them are shortened. **Read the paper.** No one independent of
ReviewProof's authors has reviewed this mapping.

## In short

ReviewProof concerns **one control: people reviewing an agent's output.** The paper treats
such review as a control that is itself to be validated. ReviewProof can supply part of the
evidence about that control, **for review of a proposal that is complete on the reviewer's
screen** (see [where they differ](#where-reviewproof-and-vital-differ)).

**It completes none of VITAL's ten evidence categories.** It can contribute part of seven, in
some cases a small part, and nothing to three. Nothing it produces is evidence about how well
the agent performs.

## The ten categories

| Evidence category | VITAL asks, in short, for | ReviewProof can contribute | Still yours | Where yours is |
| --- | --- | --- | --- | --- |
| Context-of-use specification | Intended use, users, workflow, permitted data, decisions and boundaries of the system | **Nothing.** The [intended-use statement](intended-use.md) in this pack is about ReviewProof, not your agent | All of it | **[FILL]** |
| Governance operating model | Accountability, decision gates, escalation, and who holds human control | **A small part:** roles for testing human review, in the [procedures](README.md#what-is-here), and alerts to a QA lead | Governance of the agent, its approval gates and escalation | **[FILL]** |
| Data specification | Where data comes from, its quality and representativeness, access and handling | **Nothing** | All of it | **[FILL]** |
| Design specifications and controls | Architecture, agent roles, tool permissions, blocked actions, escalation triggers and guardrails | **A small part:** on a reviewed write that goes through its guard, no write without a recorded approval or edit, and planted items are never written (REQ-013, REQ-014, REQ-048) | The design, and every other control. The guard cannot stop other routes to the target | **[FILL]** |
| Validation and revalidation protocol | Test strategy and methods, acceptance criteria, rules for results and deviations, and revalidation triggers | **Part, for human review:** planted errors as a test method; qualification pass marks fixed beforehand; thresholds; reviewers flagged when the AI system's version changes | Everything about testing the agent, deviation rules, and your other revalidation triggers | **[FILL]** |
| Validation evidence package | Test results, performance summaries, expert assessments, deviations and conclusions against the acceptance criteria | **Part, for human review:** qualification results, catch rates with 95% intervals, and the result of log verification | Evidence about the agent, your deviations and your conclusions | **[FILL]** |
| Traceability and logging specification | What is needed to reconstruct behaviour: inputs, retrieved sources, intermediate steps, tool calls, outputs, versions and decision traces | **Part:** a tamper-evident log of review actions, the system version in force, and what happened to reviewed writes ([what it records](part11-controls.md#3-the-audit-trail--1110e)) | Inputs, sources, intermediate steps, other tool calls and their results. **For real items it stores hashes, not content** | **[FILL]** |
| Operational monitoring plan | Production metrics, alert thresholds, drift, revalidation triggers and control of updates | **Part, for human review:** its metrics, thresholds and alerts, and the [metric review procedure](sop-metric-review.md) | Monitoring of the agent, including drift, and release management | **[FILL]** |
| Third-party dependency register | External models, services, vendors and data, with owners, risks and fallbacks | **Nothing.** ReviewProof and the agent library you use belong **in** your register; see the [supplier section](risk-assessment.md#4-the-supplier) | All of it | **[FILL]** |
| Transparency and auditability specification | How behaviour is made observable, including decision logic, evidence sources and execution traces | **A small part:** who was shown each reviewed item and decided what, when, and, if your application supplies them, names of what was on screen | Showing how the agent reached its output. No report analyses what reviewers could not see | **[FILL]** |

## Where ReviewProof and VITAL differ

**Read these before relying on the mapping.**

1. **What the reviewer can see.** The paper stresses that human oversight depends on where the
   reviewer enters the agent's trajectory and what they can see there: someone may reject a
   poor final answer yet be unable to detect an earlier missed source or wrong tool call that
   the review screen does not show. **A planted error has no trajectory: no trace, sources or
   history.** So a screen that shows more than the item would give planted errors away, and you
   must not use planted errors on that queue (DEP-004). Qualification sessions show only the
   item too. **Where good oversight needs the reviewer to inspect the trajectory, ReviewProof
   cannot test that review.**
2. **What is tested.** It tests whether people catch defects planted in a proposal at one
   review point. It does not test whether pause and termination controls work over long runs,
   whether reviewers have the authority and time to prevent harm, or whether the review point
   is in the right place.
3. **A catch rate is not yet validated** as a measure of how well reviewers catch real
   mistakes ([overview](README.md#what-this-pack-can-and-cannot-show)).
4. **The numbers are not VITAL's.** The paper lists human-review testing among the tests for
   higher-risk agents, but sets no planted-error share, pass mark or review frequency. Those in
   ReviewProof and its procedures are its authors' untried proposals.

## Recording your VITAL risk rating

The settings accept every rating on VITAL's two scales
([ADR-007](../docs/adr/0007-vital-ratings.md)). ReviewProof uses a rating only to choose the
default planted share, which `planted_rate` replaces. **Keep the rating and the reasons for it
in your risk assessment:** no report or log entry shows it, and verification does not cover the
copy in the database.

| Your VITAL rating | What to set | Note |
| --- | --- | --- |
| Base risk, from A1 to A10 | `vital_base_risk`, the same | |
| Base risk, a half step such as 5.5 | `vital_base_risk: A5.5` | Takes the next whole step's share |
| Adjusted category Low | `vital_adjusted_category: low` | |
| Moderate | `vital_adjusted_category: moderate` | Stored as `medium` |
| | `vital_adjusted_category: medium` | The same, as in earlier settings |
| High | `vital_adjusted_category: high` | |
| Very High | `vital_adjusted_category: very_high` | |
| Critical | `vital_adjusted_category: critical` | Recording it does not make ReviewProof suitable. The paper treats Critical as outside its routine validation route |

The default planted share, which is ReviewProof's authors' untried proposal and not VITAL's:

| Base risk | No category, Low or Moderate | High | Very High or Critical |
| --- | --- | --- | --- |
| A1 to A4 | 1% | 2% | 3% |
| A4.5 to A6 | 2% | 3% | 3% |
| A6.5 to A10 | 3% | 3% | 3% |

## Your conclusion

| | |
| --- | --- |
| Agent, and VITAL base risk and adjusted category | **[FILL]** |
| Review points where ReviewProof is used, and why a screen with only the item is enough there | **[FILL]** |
| Review points it cannot test, and how you assure them | **[FILL]** |
| Assessed by, and date | **[FILL]** |
