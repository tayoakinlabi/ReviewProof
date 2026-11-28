# Risk assessment

**Template.** Read the [overview](README.md) and complete the
[intended-use statement](intended-use.md) first.

| | |
| --- | --- |
| ReviewProof version | **[FILL]** |
| Assessed by | **[FILL: names and roles]** |
| Approved by, and date | **[FILL]** |

## 1. Method

This follows the risk-based approach in FDA's guidance
[Computer Software Assurance for Production and Quality Management System
Software](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/computer-software-assurance-production-and-quality-management-system-software)
(February 2026). **That guidance is nonbinding, and its stated scope is software used in medical
device production and quality management systems.** If you make drugs or other products, you
are borrowing its method: confirm with your quality unit which rules and procedures apply to
you, and change this template to match them.

For each feature you use:

1. Identify the **reasonably foreseeable** ways it could fail, whether or not they are likely.
2. Decide whether each failure poses a **high process risk**. In the guidance's words, that is
   when "failure to perform as intended may result in a quality problem that foreseeably
   compromises safety, meaning a medical device risk". **[FILL: your own definition, in terms of
   your product, patients and records.]** Otherwise it is **not high process risk**.
3. Choose assurance activities in proportion. The guidance names **scripted testing** (recorded
   test cases) and **unscripted testing** (such as scenario testing, error guessing and
   exploratory testing). It suggests considering more rigor, such as scripted testing, for high
   process risk.
4. Record the result (see [section 5](#5-assurance-record)).

The ratings below are **proposals from the software's authors, who do not know your process.**
Each one needs your decision and your reason. A rating depends above all on what your write
function changes, and on what you decide from the results.

"Controls in the software" lists [requirements](requirements.md). "Your controls" lists
[deployment duties](requirements.md#deployment-duties).

## 2. Risks

### Writes and records

| ID | What could go wrong | Effect | Controls in the software | Your controls | Proposed | Why | Your rating and reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RISK-001 | A planted item is written to the target system | False data in a record | REQ-002, REQ-013, REQ-014, REQ-015, REQ-019, REQ-027 | DEP-001, DEP-002 | High | Deliberately defective content could reach a record | **[FILL]** |
| RISK-002 | A real item is written without approval, with other content, or twice | Unreviewed or duplicated data in a record | REQ-014 to REQ-018, REQ-020, REQ-021, REQ-047, REQ-048, REQ-050, REQ-052 | DEP-001, DEP-016 | High | Defeats the review that protects the record | **[FILL]** |
| RISK-003 | Your code or the agent writes without going through the guard | Any of the above, with no evidence of it | **None. The software cannot prevent this** | DEP-001, and a test in your environment | High | No control in the software applies | **[FILL]** |
| RISK-004 | A write fails or half-succeeds and is repeated, or a person believes it happened when it did not | Duplicate, partial or missing data | REQ-016, REQ-018, REQ-051 | DEP-016 | High | The state of the record is uncertain until a person checks | **[FILL]** |
| RISK-005 | A fault in ReviewProof, or a busy or unavailable database, stops or delays real work | Real items are not processed | REQ-009, REQ-015, REQ-017, REQ-029 | DEP-011, DEP-016, and a way to carry on without it | Not high | Work stops visibly instead of going through unchecked. Raise this if delay itself can harm | **[FILL]** |

### Whether the results mean anything

| ID | What could go wrong | Effect | Controls in the software | Your controls | Proposed | Why | Your rating and reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RISK-006 | Reviewers can tell which items are planted | Catch rate looks better than real oversight is | REQ-010, REQ-011, REQ-012, REQ-040, REQ-041, REQ-049 | DEP-003 to DEP-007 | High | False assurance about a control you rely on | **[FILL]** |
| RISK-007 | Planted errors are unrealistic, too easy or not really defective | Misleading results; reviewers judged unfairly | REQ-001, REQ-003, REQ-004, REQ-005, REQ-027 | DEP-012 | High | The software cannot judge realism or correctness | **[FILL]** |
| RISK-008 | Too few planted errors are released | Too little data to see a problem | REQ-006, REQ-007, REQ-008, REQ-039, REQ-053 | DEP-012, and review of shortfalls | Not high | Shortfalls are recorded and missing data is not shown as a pass | **[FILL]** |
| RISK-009 | A figure, threshold or report is wrong | Quality decides on wrong information | REQ-035 to REQ-039 | DEP-015, DEP-018 | High, if decisions rest on the figures | A wrong figure could hide weak oversight | **[FILL]** |
| RISK-010 | Chance in a small sample is read as a real difference | Wrong conclusion about a person or the control | REQ-036, REQ-039, REQ-040 | DEP-013, and a procedure for acting on results | Not high | Ranges and minimum samples are shown. Raise this if results alone decide actions | **[FILL]** |
| RISK-011 | An alert is not sent or not seen | A breach is noticed late | REQ-039, REQ-040 | Review of reports on a schedule | Not high, with scheduled review | The report holds the same findings | **[FILL]** |

### Evidence and records

| ID | What could go wrong | Effect | Controls in the software | Your controls | Proposed | Why | Your rating and reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RISK-012 | Evidence is changed or removed without notice, including by someone with write access to the files | Records cannot be trusted | REQ-024 to REQ-028, REQ-033 | DEP-007, DEP-010 | High, if relied on as a quality record | Without exported heads, a rebuilt log passes | **[FILL]** |
| RISK-013 | An event is attributed to the wrong person or time | Records are not attributable or contemporaneous | REQ-020, REQ-022, REQ-025 | DEP-008, DEP-009 | High, if relied on as a quality record | The software accepts the ID and clock it is given | **[FILL]** |
| RISK-014 | A database is lost, restored apart from its pair, or damaged by an upgrade | Evidence lost or inconsistent | REQ-027, REQ-045, REQ-046 | DEP-011 | High, if relied on as a quality record | A loss may not be recoverable | **[FILL]** |
| RISK-015 | The software or settings change without assessment | The validated state is lost | REQ-044, REQ-046 | DEP-015, DEP-018 | High, if it affects a high-risk feature | Settings also change how past data is judged | **[FILL]** |

### People and data

| ID | What could go wrong | Effect | Controls in the software | Your controls | Proposed | Why | Your rating and reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RISK-016 | An unqualified reviewer, or one qualified for an older AI system version, keeps reviewing | Review by someone not shown to be able | REQ-030, REQ-031, REQ-032, REQ-034 | DEP-014 | High, if qualification is required | The software reports status but blocks no one | **[FILL]** |
| RISK-017 | Individual results are seen or used beyond their purpose | Harm to staff; legal breach; loss of trust | REQ-023, REQ-040, REQ-041, REQ-042 | DEP-007, DEP-008, DEP-013 | Assess under your privacy rules | A risk to people more than to product quality | **[FILL]** |
| RISK-018 | Regulated content leaves your systems | Disclosure of confidential or personal data | REQ-023, REQ-043 | DEP-017 | Assess under your data rules | ReviewProof sends only alerts, but your agent library or model provider may send more | **[FILL]** |

### Agent libraries

| ID | What could go wrong | Effect | Controls in the software | Your controls | Proposed | Why | Your rating and reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RISK-019 | A setting, hook or new release of an agent library lets the run carry on after a failed write, or shows the model something else than what happened | The agent retries a write, or reports work as done | REQ-051, and the guard (REQ-014 to REQ-016) | DEP-016, DEP-018 | High | **This depends on library behaviour that can change.** The guard stops a repeat of the same call, but not a new call, which needs a new review | **[FILL]** |
| RISK-020 | A library keeps or sends the agent's state, traces or pauses where reviewers can see them | Planted items can be told apart | REQ-049 | DEP-005, DEP-017 | High | As RISK-006 | **[FILL]** |

### Voiding

| ID | What could go wrong | Effect | Controls in the software | Your controls | Proposed | Why | Your rating and reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RISK-021 | Voiding is used to remove genuine misses, or a faulty planted error is voided selectively or not at all | Review looks better than it is, or people are judged unfairly | REQ-054 to REQ-058 | DEP-019, DEP-008, DEP-010 | High | The one feature that removes results from scores. The software cannot judge whether a reason is true, or who is typing | **[FILL]** |

### Drafting from real output

| ID | What could go wrong | Effect | Controls in the software | Your controls | Proposed | Why | Your rating and reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RISK-022 | Real information enters `planted.db`, in a planted error drafted from real output or written by hand | Personal or confidential data is shown to reviewers and kept in backups, and **cannot be removed** without breaking the evidence | REQ-059 to REQ-064. They do nothing for a file written by hand, and the search finds only what it was told to | DEP-020, DEP-012, DEP-007, DEP-011 | High | It cannot be undone. A person reading the item is the real check | **[FILL]** |

## 3. Your own risks

**[FILL: risks from your process, review screen, write function, target system,
infrastructure and people. Number them from RISK-101 so that they never clash with the
template's.]**

## 4. The supplier

ReviewProof is an open-source project under the Apache 2.0 license. **It comes with no vendor
contract, certification, supplier audit or support commitment.** Before 1.0.0, any release may
change its programming interface or database format, and only the latest version receives
security fixes. It is not pre-validated.

What you can examine, in the public repository:

| Evidence | Where |
| --- | --- |
| Source code and its history | The repository |
| Design records, with what was observed when a decision depended on it | [`docs/adr/`](../docs/adr/) |
| Automated tests, including those for the execution guard | [`tests/`](../tests/) |
| Which tests are tagged with each requirement | [Traceability matrix](trace-matrix.md) |
| A record of one run of every test with every adapter, made for each change. GitHub keeps it for a limited time, so save the one you rely on | The `validation-evidence` file on the repository's Actions page |
| Automated test runs on Linux and Windows, and for each adapter | The repository's Actions page |
| Changes in each release, with what a validated user would retest | [Changelog](../CHANGELOG.md) |
| How to report a vulnerability | [Security policy](../SECURITY.md) |

**The project's tests are the developer's evidence, not your validation.** Decide how far you
rely on them, and what you test yourself. **[FILL: your supplier assessment and its
conclusion.]**

## 5. Assurance record

For each feature in use, record what the guidance recommends:

| Item | Record |
| --- | --- |
| Intended use of the feature | **[FILL, or refer to the intended-use statement]** |
| Result of the risk-based analysis | **[FILL, or refer to section 2]** |
| Assurance activities, with a description of the testing | **[FILL]** |
| Issues found, such as deviations, defects and failures, and how each was resolved or justified | **[FILL]** |
| Conclusion: is the software acceptable for this intended use? | **[FILL]** |
| Who tested or assessed, and when | **[FILL]** |
| Review and approval, where appropriate | **[FILL]** |

## 6. Reassess when

- the ReviewProof, Python or agent library version changes;
- the intended use, the target system, the write function or the review screen changes;
- the AI system's model, prompt or tools change in a way that affects what is reviewed;
- a planted item reaches a target system, verification fails, or the guard reports a failed
  check;
- **[FILL: your periodic review interval]** has passed.
