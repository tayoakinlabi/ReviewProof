# Procedure: reviewer qualification

**Template.** For the quality unit that decides who may review output from an AI system. Read
the [overview](README.md) and complete the [intended-use statement](intended-use.md) first. Text
marked **[FILL]** is yours to write. Values marked *proposed* are the authors' starting points.
**They have not been tried with real reviewers.** Check every statement, and change what is not
true for you.

| | |
| --- | --- |
| Procedure number and version | **[FILL]** |
| Owner | **[FILL]** |
| Approved by, and date | **[FILL]** |
| Effective date, and review interval | **[FILL]** |
| ReviewProof version | **[FILL]** |

## 1. Purpose and scope

To show, before and while a person reviews, that they catch seeded defects without rejecting
correct work. In a **session**, one reviewer decides a mix of planted errors and correct items,
away from the live queue. Nothing in it is carried out, and its pass mark is fixed before it
starts.

A pass shows how a person did on these items under these conditions. It does not show how they
will do on real work, and it does not replace training on the process.

Queues covered: **[FILL]**. This procedure addresses deployment duty DEP-014, and parts of
DEP-008 and DEP-012, in the [requirements](requirements.md#deployment-duties). Read the
[qualification guide](../docs/guides/qualification.md) for the file formats.

## 2. Roles

| Role | Responsible for | Who |
| --- | --- | --- |
| Quality lead | Pass marks, who may review, and what follows a result | **[FILL]** |
| QA administrator | Preparing, running and recording sessions | **[FILL]** |
| Item author and checker | Writing and independently checking items | **[FILL]** |
| Reviewer | Answering alone, and telling no one about the items | **[FILL]** |

## 3. Who needs a pass, and when

| VITAL base risk of the queue | *Proposed:* qualify | Your rule |
| --- | --- | --- |
| A1 to A4 | Before a person first reviews | **[FILL]** |
| A5 and A6 | Before first review, and after each change of the AI system's version | **[FILL]** |
| A7 and above | As above, and every **[FILL]** | **[FILL]** |

A High, Very High or Critical adjusted category moves a queue up one row (*proposed*). The
settings have no Very High or Critical, and refuse half steps: see
[how to record your VITAL rating](vital-mapping.md#recording-your-vital-risk-rating).

**ReviewProof reports status and blocks no one** (DEP-014). **[FILL: who may review without a
current qualification, if anyone, under what supervision and for how long, and how your
application or procedure enforces it.]**

```bash
rp qualify status --queue QUEUE-NAME --reviewer REVIEWER-ID
```

It answers `not qualified`, `qualified` or `requalification required`.

**When ReviewProof withdraws a pass:** only when the queue's registered AI system version
changes, meaning the model, model version, prompt version or tool schema. A pass counts only if
its session started after the latest change. Record a change in the settings file, or with
`rp system register` **and** the settings file, which otherwise puts the old version back.

**A pass never expires with time.** A changed procedure, item format or review screen, a long
absence, or poor live results do not withdraw it either. Set your own triggers, track them in
**[FILL: your training system]**, and treat that record as the one that decides:

| Trigger you apply | Interval or condition |
| --- | --- |
| Periodic requalification | **[FILL]** |
| Absence from this review work | **[FILL]** |
| Change to a procedure that decides what is correct | **[FILL]** |
| Change to the item format or review screen | **[FILL]** |
| Concern from [metric review](sop-metric-review.md), after systems causes are ruled out | **[FILL]** |

## 4. Preparing items

1. **Planted errors for qualification** are written, checked and approved as in the
   [planted-error procedure](sop-planted-errors.md), with `purpose: qualification`. They never
   enter a live queue.
2. **Correct items** are synthetic or de-identified, in the queue's item format. **ReviewProof
   asks for no approval of them.** A second qualified person confirms that each is entirely
   correct, and signs: a wrong "correct" item fails good reviewers.
3. Make the items as hard as real work, and cover the kinds of defect that matter.
4. Keep a reserve. A reviewer is never given a planted error from an earlier session of theirs.
   ReviewProof does not stop another reviewer getting it, or the same person meeting a correct
   item again. **[FILL: how you rotate items among people who can talk to each other.]**

## 5. Setting the pass mark

Write the plan file, with its reason, **before** the session. A plan needs at least 10 planted
errors and 5 correct items. ReviewProof accepts any `min_caught` from 1, and a
`max_false_rejects` of up to half the correct items. **Those limits only stop a person passing
by answering everything the same way. They do not make a pass mark meaningful.**

With few items, luck matters. The chance of catching enough of **10** planted errors, if each is
caught independently with the same chance:

| Reviewer's real catch rate | Mark of at least 8 | Mark of at least 9 | Mark of 10 |
| --- | --- | --- | --- |
| 70% | 38% | 15% | 3% |
| 80% | 68% | 38% | 11% |
| 90% | 93% | 74% | 35% |
| 95% | 99% | 91% | 60% |

So a mark of 8 lets through about two in three people who catch 80%, and a mark of 9 turns away
about one in four who catch 90%. Real items differ in difficulty, so treat these figures as a
guide. The chance of failing on correct items comes on top. More items give a clearer answer.

| Decision | Value and reason |
| --- | --- |
| Planted errors in a session, and kinds covered | **[FILL]** |
| Correct items in a session | **[FILL]** |
| `min_caught` | **[FILL]** |
| `max_false_rejects` | **[FILL]** |
| Any kind of defect that must not be missed. ReviewProof applies no such rule: check the result by kind yourself | **[FILL]** |

Never change a mark after seeing answers.

## 6. Running a session

1. Confirm the reviewer's identity, and use their pseudonymous ID (DEP-008).
2. Set the conditions, and record them. ReviewProof controls none of them.

   | Condition | Yours |
   | --- | --- |
   | Supervision, and no discussion or help | **[FILL]** |
   | Procedures and records the reviewer may consult: those they have in real work | **[FILL]** |
   | Time allowed | **[FILL]** |
   | Screen: as close to the real one as possible | **[FILL]** |

3. Start it. A reviewer can have one unfinished session on a queue.

   ```bash
   rp qualify start --queue QUEUE-NAME --reviewer REVIEWER-ID --plan plan.yaml --out sheet.json
   ```

4. The worksheet holds a token and the content of each item, in random order, with nothing to
   say which are defective. It holds planted errors all the same. **[FILL: how copies and answer
   files are controlled and destroyed.]**
5. Record each answer exactly as given. **[FILL: if someone copies answers into the file, who
   checks the copy.]** With your own screen, your engineer can record answers through the Python
   interface, including a reviewer's own judgment before they see the AI's answer.
6. Finish it, and **keep what is printed**: no command shows a session's result again. The
   file below is confidential.

   ```bash
   rp qualify finish --queue QUEUE-NAME --answers answers.yaml > result.txt
   ```

   To stop early, run this with the session ID. It counts as not passed, and its planted errors
   count as used by this reviewer:

   ```bash
   rp qualify abandon --queue QUEUE-NAME SESSION-ID
   ```

7. Check the records, then file the result:

   ```bash
   rp verify --planted planted.db
   ```

## 7. After a session

1. A result is **one person's score**. Share it with them, and with others only under the
   [privacy rules](sop-metric-review.md#9-reviewer-privacy-and-disclosure).
2. **[FILL: whether the reviewer is told which items they got wrong.]** Items they discuss with
   others cannot safely be used again.
3. **Passed:** record it in **[FILL]**, with the AI system version and your own expiry date.
4. **Not passed:** first check for causes outside the person: a doubtful item, the screen, the
   conditions or missing training. Then **[FILL: training or coaching, what the person may do
   meanwhile, how soon they may retake, how many times, and what happens after that]**. A retake
   needs planted errors they have not had. Use new correct items too.
5. **A faulty item:** follow
   [section 10 of the planted-error procedure](sop-planted-errors.md#10-a-planted-error-that-is-wrong-or-recognisable).
   ReviewProof cannot rescore or cancel a finished session. Record your decision beside it, and
   run a new session if needed.

## 8. Records

| Record | Where | Kept for |
| --- | --- | --- |
| Sessions, items, answers, pass marks and results | `oversight.db` and `planted.db` | **[FILL]** |
| Plan with the reason for its pass mark, and correct-item checks | **[FILL]** | **[FILL]** |
| Printed result, session conditions, and who ran it | **[FILL]** | **[FILL]** |
| Training and qualification record, with expiry | **[FILL]** | **[FILL]** |

Plans and item files identify defective items. Give reviewers no access to them.
