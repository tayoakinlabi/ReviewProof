# Procedure: metric review and reviewer privacy

**Template.** For the quality lead and those who read ReviewProof's results. Read the
[overview](README.md) and complete the [intended-use statement](intended-use.md) first. Text
marked **[FILL]** is yours to write. Values marked *proposed* are the authors' starting points.
**They have not been tried on a real review queue.** Check every statement, and change what is
not true for you. Section 9 is not legal advice.

| | |
| --- | --- |
| Procedure number and version | **[FILL]** |
| Owner | **[FILL]** |
| Approved by, and date | **[FILL]** |
| Effective date, and review interval | **[FILL]** |
| ReviewProof version | **[FILL]** |

## 1. Purpose and scope

To look at review results on a schedule, decide what to do about them, and protect the people
they describe. The figures are defined in the
[command reference](../docs/cli.md#how-the-figures-are-defined).

Queues covered: **[FILL]**. This procedure addresses deployment duty DEP-013, and parts of
DEP-007, DEP-010 and DEP-015, in the [requirements](requirements.md#deployment-duties). It does
not cover what to do when a write fails (DEP-016).

## 2. Roles

| Role | Responsible for | Who |
| --- | --- | --- |
| Quality lead | Holding reviews, deciding on actions and approving threshold changes | **[FILL]** |
| QA administrator | Verifying the records, making reports and storing them | **[FILL]** |
| Process owner | Acting on causes in the process, screen and workload | **[FILL]** |
| Privacy or works-council contact | Reviewing [section 9](#9-reviewer-privacy-and-disclosure) | **[FILL]** |

## 3. Schedule

| Activity | *Proposed* | Yours |
| --- | --- | --- |
| Alerts, run automatically | Weekly | **[FILL]** |
| Queue review: sections 4 to 7 | Every two weeks at the start, then monthly | **[FILL]** |
| Export the log's head to storage that cannot be rewritten | Each review, at least (DEP-010) | **[FILL]** |
| Individual results | Only for the reasons in [section 9](#9-reviewer-privacy-and-disclosure) | **[FILL]** |
| Thresholds, privacy rules and [risk assessment](risk-assessment.md) | Yearly, and after a change | **[FILL]** |

**Alerts do not replace a review.** They report only thresholds that are breached. Nothing is
sent for a measure that cannot be judged, a shortage of planted errors, a failed write or most
refused executions.

```bash
rp alerts --since 4w --send
```

Without `--since`, the check covers all recorded history, so one bad week long ago is reported
every time. A period that is too short leaves measures without enough data. Exit status 1 means
the records did not verify. Exit status 3 means an alert was not delivered. **[FILL: the
period, who runs this, who notices if it fails, and where each alert and its follow-up are
recorded.]** ReviewProof does not record that anyone acted on an alert.

## 4. Check the evidence first

1. Verify against the head exported at the last review, and export today's under a new name.
   At the first review there is no earlier one to name.

   ```bash
   rp verify --planted planted.db --expect-head head-previous.json --export-head head-today.json
   ```

2. Store the new head in **[FILL: storage that ReviewProof and its administrators cannot
   rewrite]**.
3. **If verification fails: stop.** Use no scores, including from earlier reports, until the
   failure is explained. Keep copies of both databases as they are, and open **[FILL: quality
   record]**.

## 5. Make the report

For the period since the last review, or longer where that gives too little data:

```bash
rp report --since 4w --expect-head head-previous.json --out report.html
```

Keep the report as a record, **with the settings file used to make it**. Limits and the minimum
review time are applied to past decisions as the settings stand when the report is made, and
each report lists the limits it used.

## 6. Read it in this order

1. **The evidence banner:** it says whether an earlier head was checked.
2. **Health of the planted-error programme.** Act on shortages and held-back planted errors as
   in the [planted-error procedure](sop-planted-errors.md#8-watching-the-stock). A weak program
   gives weak figures. Look into **execution refused for other reasons** and **real executions
   that failed**, under **[FILL: your error-handling procedure]**: someone checks the target
   system after a failed write.
3. **The thresholds.** Each is a breach, within its limit, or **not enough data, which is not a
   pass**. "Withheld" means the figure would describe one person.
4. **Workload and attention,** before drawing any conclusion about people.
5. **By version of the AI system and by site,** where shown.

**Chance.** Read the likely range beside each rate. A flag is a reason to look, not a finding.
With 10 planted errors, a reviewer whose real catch rate is 90% falls below an 80% target about
7% of the time, and one at 85% about 18%. With several rules and queues, some breaches will be
luck. "Could be chance" and "clearly below" mean different things.

**Not enough data for [FILL] reviews in a row** is itself a finding: you cannot say whether the
review works. Check the stock, the planted share, the period and the volume of work.

## 7. Respond: systems first

Before looking at any person, ask what in the **system** could cause the result: workload,
interruptions, time pressure, the review screen, how the task is designed, unclear procedures,
training, a change in the AI system, or a poor planted error.

| Finding (default limit) | Look at | Your required actions and time limit |
| --- | --- | --- |
| Catch rate below 80% over the latest 50 planted errors | All of the above. Is review still an adequate control for this process? | **[FILL, including when you stop relying on the review, and what protects the process then]** |
| One kind of defect caught below 60% | What a reviewer needs to see to catch it. Training and screen | **[FILL]** |
| More than 20% of a week's approvals faster than the minimum time | Workload and batching. Whether the minimum time is realistic. How the screen records showing | **[FILL]** |
| Over 98% agreement while catch rate falls | Approving on habit. Changes in the AI system's output | **[FILL]** |
| Catch rate 20 points lower late in sessions | Session length, breaks and the order of work. The session is worked out from decision times, not shifts | **[FILL]** |
| Daily load above your cap, if set | Staffing and distribution of work | **[FILL]** |
| A safety check stopped execution | **Urgent.** Run `rp verify`. Find out what was attempted, and whether the target system changed | **[FILL]** |

After a change made to fix a finding, say how and when you will check that it worked.

**Do not tune away a breach.** A threshold changes only under change control, with its reason
recorded before the new value is used (DEP-015). Keep the earlier reports.

## 8. Record the review

| | |
| --- | --- |
| Date, queue and period | |
| Attendees | |
| Verification result, and where the head was stored | |
| Report file, and settings version | |
| Planted-error program: findings | |
| Breaches, and measures that could not be judged | |
| Systems causes considered | |
| Decisions, actions, owners and dates | |
| Earlier actions checked, and their effect | |
| Is the review still an adequate control? Reason | |
| Quality lead, signature and date | |

Records of a review hold queue-level results. Keep anything about a named or numbered person in
a separate, restricted record.

## 9. Reviewer privacy and disclosure

### 9.1 What is recorded about a reviewer

Under the ID your application supplies: which items were shown and decided, when, the
decisions, which fields were edited, feedback sent, and qualification answers, scores and
status. `planted.db` also records which reviewer a planted error was released for, where your
application named one, and any judgment recorded before the AI's answer was shown. Review times and patterns of work can be
worked out from these.

**Use pseudonymous IDs,** never names or email addresses. **[FILL: who keeps the list that
links IDs to people, who may use it, and when entries are destroyed.]** Pseudonymous data may
still be personal data.

### 9.2 Tell reviewers before the program starts

Reviewers know that the program exists, never which items are planted. Tell them, in writing:

- [ ] that some items are planted errors, why, and that they are never carried out;
- [ ] what is recorded about them, including review times;
- [ ] what results are used for, and what they are not used for;
- [ ] who can see their own results, and from how many planted errors;
- [ ] what feedback they get, and when;
- [ ] that they may be asked which items they suspected;
- [ ] how long records are kept, and what cannot be erased;
- [ ] how to ask a question, object or raise a concern: **[FILL]**.

Record that each reviewer was told: **[FILL]**.

### 9.3 Purpose limit

Individual results are used only for **qualification, training and planning workload**. **No
discipline on the strength of these results without an investigation of the root cause,**
including the systems causes in section 7. Other uses: **[FILL: none, or what, with the review
that allows it]**.

### 9.4 Individual results

Make them only when **[FILL: reasons, for example a qualification or training decision, or an
investigation into a queue finding after systems causes were checked]**, record who asked and
why, and limit who receives them to **[FILL]**.

```bash
rp report --since 4w --individuals --out individuals.html
```

- No individual score appears below 10 planted errors decided at first sight in the period.
  Your settings can raise this minimum, never lower it. Workload is always shown.
- Read each result beside the person's workload, with its likely range.
- Discuss a finding with the person before acting on it.

### 9.5 What the software protects, and what it does not

- A report names no reviewer unless you ask for individual results. Alerts go to the QA lead
  and hold queue-level findings only.
- A queue's, version's or site's figure that rests on one reviewer with fewer planted errors
  than the minimum is withheld, with the checks made from it. Counts of work done are shown.
- **Subtraction can still reveal a hidden figure,** from a total and its other parts, from two
  reports with different periods, or from a group's figure and the other members'. In a small
  team, a queue-level figure may point to a person. Limit who sees any report.
- **The minimum applies only to ReviewProof's reports.** Anyone who can read the databases can
  work out anything about anyone. So can anyone running `planted_records.py uses`.
- `--individuals` and CSV output make files that need the same protection (DEP-007).

### 9.6 Keeping and erasing

**The log refuses updates and deletions.** One person's entries cannot be removed without
breaking verification, and ReviewProof has no command to erase or expire them. Decide before
you start how you will handle retention and any request to erase, for example by destroying the
link between ID and person, and have this reviewed. Retention period: **[FILL]**.

### 9.7 Reviews required before use

| Review | Needed? Outcome and date |
| --- | --- |
| Data protection, including an impact assessment where required, for example under EU or UK GDPR | **[FILL]** |
| Works council, union or staff consultation | **[FILL]** |
| Employment law on monitoring staff | **[FILL]** |
| Human resources policy on how results may be used | **[FILL]** |

## 10. Records

| Record | Where | Kept for |
| --- | --- | --- |
| Reports, with the settings used | **[FILL]** | **[FILL]** |
| Exported heads | **[FILL: storage that cannot be rewritten]** | **[FILL]** |
| Review records, alerts and actions | **[FILL]** | **[FILL]** |
| Individual reports and who received them | **[FILL: restricted]** | **[FILL]** |
| What reviewers were told, and privacy reviews | **[FILL]** | **[FILL]** |
