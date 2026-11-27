# Intended-use statement

**Template.** Read the [overview](README.md) first. Text marked **[FILL]** is yours to write.
Check every other statement against your own use, and change what is not true for you.

| | |
| --- | --- |
| System name in your inventory | **[FILL]** |
| ReviewProof version | **[FILL]** |
| Site or business unit | **[FILL]** |
| Document owner | **[FILL]** |
| Process owner | **[FILL]** |
| Approved by, and date | **[FILL: names, signatures, dates]** |

## 1. What the software is

ReviewProof is a Python library with a command-line tool. It tests whether the people who
review AI output catch errors. Your staff write **planted errors**: items with one known defect.
ReviewProof mixes them into a queue of real proposals, blocks them from being written through its
guard, records each review step in a log that shows tampering, and reports how many were caught.

It is **not** an approval or workflow engine, has no review screen, does not judge whether a real
AI proposal is correct, and does not check who its users are.

## 2. Intended use

Proposed statement. Edit it to match what you will actually do:

> ReviewProof is used in **[FILL: process, for example deviation triage]** to measure how often
> reviewers of output from **[FILL: AI system]** catch seeded defects, to qualify reviewers with
> seeded-defect sessions, and to keep evidence of both. Quality **[FILL: role]** uses the results
> to **[FILL: the decisions they inform, for example whether review of this AI system remains an
> adequate control, what training is needed, and how workload is set]**.

State the decisions the results inform, and those they do not. A catch rate shows how reviewers
did on the planted errors you wrote. It does not show that any real item was correct.

**Is a real record changed through ReviewProof?** Your write function is called only through its
guard. **[FILL: say what that function writes, to which system, and whether that system holds GxP
records.]**

## 3. Uses it is not intended for

- Judging an individual's job performance, or discipline. Individual results are for
  qualification and coaching, under a written policy.
- Replacing any review, approval or electronic signature your process requires.
- A review screen that shows material beyond the item itself, on a queue with planted errors.
- A review done by the person who is using the AI system and can watch it act.
- **[FILL: other exclusions]**

## 4. Features and their use

Computer software assurance looks at each feature's own intended use. For each, record whether
you use it, and how. "Directly" below means used directly as part of the quality system, such
as maintaining a quality record. "Support" means it only supports that work. The last column is
a **suggestion to challenge**, not a determination.

| Feature | What it does | Used here? | How | Suggested |
| --- | --- | --- | --- | --- |
| F1 Planted-error library | Import, approval and retirement of planted errors | **[FILL]** | **[FILL]** | Directly: the approved errors define what is measured |
| F2 Release of planted errors | Mixes planted errors into the queue | **[FILL]** | **[FILL]** | Directly: affects what reviewers see |
| F3 Execution guard | Decides whether your write function is called | **[FILL]** | **[FILL]** | Directly: stands in front of your record |
| F4 Event log and verification | Records review events and detects changes | **[FILL]** | **[FILL]** | Directly, if you rely on the log as a quality record |
| F5 Metrics and reports | Catch rate and other figures | **[FILL]** | **[FILL]** | Directly, if Quality decides from them |
| F6 Alerts | Sends threshold breaches to the QA lead | **[FILL]** | **[FILL]** | Support, if reports are also reviewed on a schedule |
| F7 Reviewer qualification | Seeded-defect sessions and status | **[FILL]** | **[FILL]** | Directly, if it decides who may review |
| F8 Paused agents and adapters | Connects agent libraries to the guard | **[FILL: which adapter]** | **[FILL]** | Directly, as F3 |

## 5. Records

| Record | Where | Holds |
| --- | --- | --- |
| Event log | `oversight.db` | Submissions, showings, decisions, executions, blocks, feedback, AI system changes and qualification events. For real items, salted hashes, not content. |
| Item registry | `planted.db` | Which items are planted, target IDs, trace hashes and names of what screens showed |
| Planted errors | `planted.db` | Their full content, approvals and retirement |
| Qualification sessions | `planted.db` | The full content of session items, and any judgment recorded before the AI's answer was shown |
| Exports | Where you put them | Reports, exported log heads and qualification worksheets |

**[FILL: which of these are GxP records in your system, their retention periods, and where the
system of record for the reviewed content is. ReviewProof does not keep the content of real
items.]**

## 6. Users

| Role | Does | In your organisation |
| --- | --- | --- |
| Quality lead | Reads reports and alerts, sets thresholds | **[FILL]** |
| QA administrator | Manages planted errors and qualification sessions | **[FILL]** |
| Planted-error author and approver | Writes and approves; never both for one error | **[FILL]** |
| Engineer | Integrates and operates the software | **[FILL]** |
| Reviewer | Reviews items. Has no access to the databases | **[FILL]** |
| Validation lead | Adapts and carries out this pack | **[FILL]** |

## 7. Operating environment

| | Tested by the project | Yours |
| --- | --- | --- |
| Python | 3.10 to 3.14 | **[FILL]** |
| Operating system | Linux and Windows, in the project's automated tests | **[FILL]** |
| Database | SQLite, as supplied with Python. One writer at a time | **[FILL: SQLite version, storage]** |
| Required dependency | PyYAML | **[FILL: version]** |
| Agent library, if used | See each adapter's guide for the tested version. Pydantic AI and Microsoft Agent Framework need Python 3.11 or later | **[FILL: library and version, or none]** |
| Review application and write function | Not part of ReviewProof | **[FILL]** |

## 8. Dependencies on your system

The intended use holds only if the [deployment duties](requirements.md#deployment-duties) are
met. The ones that most affect whether results mean anything:

- every write goes through the guard (DEP-001);
- reviewers cannot tell planted items from real ones (DEP-003 to DEP-006);
- the planted errors are realistic and have real defects (DEP-012);
- the log's head is exported to storage that cannot be rewritten (DEP-010).

## 9. Known limitations

- **The method has not been validated on a real review queue.** The project's tests show that
  the software does what it says, using synthetic data and simulated reviewers. They do not
  show that a low catch rate on planted errors means weak oversight in your work. **[FILL: how
  you will judge that.]**
- ReviewProof cannot stop an application that writes to the target system without it.
- A planted error has no agent trace, sources or history, so a review screen that shows those
  gives planted errors away.
- It does not authenticate anyone, and provides no electronic signatures.
- Its times come from the clock of the machine it runs on.
- It cannot detect a completely rebuilt log without a head exported earlier.
- With few planted errors, results are strongly affected by chance.
- Retiring a planted error leaves its past results in every score, including one found to be
  wrong. ReviewProof cannot exclude them.
- A reviewer's qualification does not expire with time.
- **[FILL: limitations you found]**
