# Procedure: planted-error program

**Template.** For the quality unit that runs a planted-error program. Read the
[overview](README.md) and complete the [intended-use statement](intended-use.md) first. Text
marked **[FILL]** is yours to write. Numbers marked *proposed* are the authors' starting
points. **They have not been tried on a real review queue.** Check every statement, and change
what is not true for you.

| | |
| --- | --- |
| Procedure number and version | **[FILL]** |
| Owner | **[FILL]** |
| Approved by, and date | **[FILL]** |
| Effective date, and review interval | **[FILL]** |
| ReviewProof version | **[FILL]** |

## 1. Purpose and scope

To keep a supply of realistic, correctly judged and secret **planted errors** for each queue:
items with one known defect, written by our staff and mixed into real review work. A catch rate
means no more than the planted errors behind it.

Queues covered: **[FILL]**.

This procedure addresses deployment duty DEP-012, and parts of DEP-007 and DEP-008, in the
[requirements](requirements.md#deployment-duties). Reviewing the results is in the
[metric review procedure](sop-metric-review.md). Planted errors for qualification also follow
the [qualification procedure](sop-reviewer-qualification.md).

## 2. Roles

| Role | Responsible for | Who |
| --- | --- | --- |
| Program owner (QA) | This procedure, the stock, realism checks and retirement | **[FILL]** |
| Author | Writing planted errors | **[FILL: required knowledge and training]** |
| Approver | Judging each one. Never its author | **[FILL: required knowledge and training]** |
| QA administrator | Running the commands, and controlling access to the files | **[FILL]** |

Nobody who reviews on a queue writes, approves or can read that queue's planted errors.
**[FILL: how you meet this where few people know the process, or why you cannot and what you do
instead.]**

## 3. Before a queue starts

1. Record the settings for each queue, under change control (DEP-015):

   | Setting | Meaning | Value and reason |
   | --- | --- | --- |
   | `vital_base_risk`, `vital_adjusted_category` | Set the planted share unless you set it: 1% of items for A1 to A4, 2% for A5 and A6, 3% for A7 and above, one step higher for a High adjusted category (*proposed*). VITAL has ratings these settings cannot hold: see [how to record yours](vital-mapping.md#recording-your-vital-risk-rating) | **[FILL]** |
   | `planted_rate` | Replaces that share. At most `0.5` | **[FILL]** |
   | `gxp` | `true`, the default, needs two approvers for each planted error. `false` needs one | **[FILL]** |
   | `feedback` | Whether reviewers learn outcomes at once or in batches. Your application must ask for the feedback and show it | **[FILL]** |

2. Tell reviewers about the program before it starts, as in the
   [privacy rules](sop-metric-review.md#9-reviewer-privacy-and-disclosure).
3. Restrict `planted.db`, planted-error files, their drafts and their backups. Reviewers have
   no access. Run `rp plant` where reviewers cannot see. **[FILL: where files are kept, who has
   access, and what happens to them after import.]**
4. Work out the stock ([section 4](#4-how-many-to-keep)), then write, approve and load it.

## 4. How many to keep

A reviewer meets roughly the number of items they review, multiplied by the planted share. For
example, 100 items a week at 2% is about 2 a week, or 16 in eight weeks. The real number is
random and can be well above or below this. No individual result is shown below 10 planted
errors.

| For each queue | Value |
| --- | --- |
| Items one reviewer handles in the period you report on | **[FILL]** |
| Planted errors that reviewer should meet in it (items multiplied by share) | **[FILL]** |
| Reserve for chance, retirement and delay in approval | **[FILL]** |
| Restock when ready planted errors fall below | **[FILL]** |
| Separate stock for qualification | **[FILL]** |

Keep at least as many different ones as one reviewer should meet, plus the reserve. ReviewProof
reuses planted errors. It skips ones a reviewer has met only when your application names the
reviewer at submission, as it usually cannot for a shared queue. A repeat counts in no score.

Cover each kind of defect that can happen in the process: wrong value, omission, wrong
classification, unsupported claim, wrong target and policy breach. Include harder ones. A kind
is judged only from 10 or more planted errors, by default.

## 5. Writing

Follow the [writing guide](../docs/guides/writing-planted-errors.md). In particular:

1. **Secret:** never discuss a particular planted error where reviewers can hear or read it.
2. **No real data:** synthetic or de-identified values only.
3. **Never the public examples,** or lightly edited copies of them.
4. **One defect,** findable from what the reviewer sees or is expected to check, with an answer
   two senior reviewers would agree on, and a real consequence if missed.
5. **Looks real,** with identifiers that cannot match a real record, now or later.

**[FILL: where authors get the pattern of real output without copying real data, how defects
are chosen (for example from your deviation or complaint history), and how identifiers are
kept from colliding.]**

Drafts made with the drafting helpers copy a correct item. The author works through the whole
checklist in the draft before it goes to approval.

## 6. Approval

Each approver checks the file independently, and records the result:

| Check | Yes or no |
| --- | --- |
| The item is truly defective, in the way the file says | |
| There is exactly one defect | |
| A careful reviewer could find it with what they have | |
| The expected decision, and the defect field, are what our procedures require | |
| It looks like a real item, and nothing marks it out | |
| Its identifiers cannot match a real record | |
| It holds no real data | |

| | |
| --- | --- |
| Planted error ID | |
| Approver, signature and date | |

A single "no" means rejection: correct it, and import it under a new ID if it was imported.
**Never approve an item you doubt.** A planted error that is not really wrong counts good
reviewers as missing it, and its results cannot be taken out again ([section 10](#10-a-planted-error-that-is-wrong-or-recognisable)).

ReviewProof checks that approvers' names differ from the author's, and from each other where two
are needed. **It cannot prove who typed a name.** The signature in **[FILL: your quality
system]** is the approval record.

## 7. Loading

The QA administrator imports each approved file, records each approval under the approver's own
ID, and checks the list:

```bash
rp plant import PE-0001.yaml
rp plant approve PE-0001 --approver APPROVER-ID
rp plant list --queue QUEUE-NAME
```

Once a planted error has been used, its approvals cannot change, and its content can never be
changed after import.

## 8. Watching the stock

At each [metric review](sop-metric-review.md), read **Health of the planted-error programme**
in the report, and run `rp plant list`:

| Sign | Meaning | Action |
| --- | --- | --- |
| "Times a planted error was due but none could be used" is above 0 | The queue ran short, so fewer were released than the settings say | Add planted errors. Look for items waiting long for a decision: a planted error is not released again while a copy waits |
| Ready ones below the restock level | Shortage coming | Write and approve more |
| "Held back", or `held back:` in the list | It no longer fits the item format | Retire it, and write a replacement |
| Repeat sightings rising | Reviewers are meeting the same ones | Add and rotate |

**Alerts do not report shortages.** Someone has to read the report.

## 9. Realism, rotation and changes

**Realism check, every [FILL] (*proposed:* every two weeks at the start).**

1. Ask each reviewer separately which items they suspected were planted, and why.
2. **Do not confirm or deny any guess,** then or later.
3. Compare the answers with the planted errors, in private.
4. If something other than the defect gave one away, retire it, find what the others share with
   it, and correct how they are written. Finding the defect itself is a reviewer doing the job.
5. Record the check, including when nothing was found.

**Retire a planted error** when a reviewer can recognise it, when it is talked about, when it
is held back, when its dates or style no longer look current, or when
[section 10](#10-a-planted-error-that-is-wrong-or-recognisable) applies. **[FILL: any maximum
age or number of uses.]**

```bash
rp plant retire PE-0001 --by YOUR-ID --reason "recognised by a reviewer"
```

Retiring is permanent. `rp plant list` does not show who retired one or why, and **log
verification does not cover retirement records**. Record each retirement in **[FILL: your
quality system]** too. This script lists every planted error with who wrote, approved and
retired it, when and why, without its content:

```bash
python validation-pack/planted_records.py register planted.db
```

**Review the stock after a change** to the AI system's model, prompt or tools (is its style
still matched?), to the item format (some may be held back), to the review screen, or to a
procedure that decides what is correct. **ReviewProof cannot detect a procedure change.**
Before such a change takes effect, check every affected expected decision, and retire the ones
that are no longer right.

## 10. A planted error that is wrong or recognisable

This applies if a planted error was not really defective, had more than one defect, expected the
wrong decision, or could be recognised by something other than its defect.

1. **Retire it at once.**
2. **Its past results remain in every score.** Retiring does not remove them, ReviewProof has no
   way to exclude them, and reports do not name planted errors.
3. Verify the records, then list everyone known to have been given, shown or to have
   decided on it, including in qualification sessions:

   ```bash
   rp verify --planted planted.db
   python validation-pack/planted_records.py uses PE-0001 oversight.db planted.db
   ```

   A live copy with nothing recorded names a reviewer only if your application did. The
   list shows what individuals did. Handle it under the
   [privacy rules](sop-metric-review.md#9-reviewer-privacy-and-disclosure).
4. Open **[FILL: a deviation or other quality record]**. Assess every report, decision and
   qualification result that used it, and note the effect on each. A catch rate that includes it
   is not corrected by ReviewProof: state the corrected reading beside the report.
5. Take no decision about a person from a result it affected. Where it affected a qualification
   result, decide on a new session with other items.
6. If reviewers were given feedback on it, tell them what was wrong with the feedback.
7. Find why approval missed it.

**If a planted item ever reaches a target system,** stop using the integration, keep the
databases as they are, and open **[FILL: your highest-priority quality record]**. Do not restart
until the cause is found and the first [test script](test-scripts.md#part-b-tests-in-your-own-deployment),
MT-001, passes again.

## 11. Records

| Record | Where | Kept for |
| --- | --- | --- |
| Planted errors, approvals and retirements | `planted.db` | **[FILL]** |
| Signed approval checklists | **[FILL]** | **[FILL]** |
| Settings and their change records | **[FILL]** | **[FILL]** |
| Realism checks, retirements and restocking | **[FILL]** | **[FILL]** |
| Investigations under section 10 | **[FILL]** | **[FILL]** |

All of these identify planted errors. Give reviewers no access to any of them.
