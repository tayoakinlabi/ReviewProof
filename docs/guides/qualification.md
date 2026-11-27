# Qualifying reviewers

For the QA staff who run qualification sessions, and the engineer who supports them.

> **Status:** ReviewProof is pre-release and not pre-validated. Decide your pass marks, and who
> may review without a pass, in your own procedures.

A session gives one reviewer a mix of **planted errors** and **correct items**, away from the
live queue. Nothing in it is executed. The pass mark is fixed before it starts.

## 1. Write the items

- **Planted errors for qualification** are ordinary [planted-error files](writing-planted-errors.md)
  with one extra line: `purpose: qualification`. They are approved the same way. They are never
  used in a live queue, and live ones are never used in a session.
- **Correct items** are in your item format, synthetic or de-identified, with nothing wrong.
  Put them in a JSON or YAML list. Give them identifiers that look normal and match no real or
  planted item. Have someone check that each really is correct: a "correct" item with a mistake
  in it fails good reviewers.

Use items as hard as real ones. A session anyone could pass shows nothing.

## 2. Write the plan

```yaml
queue: deviation-triage
planted_errors: [Q-001, Q-002, Q-003, Q-004, Q-005, Q-006, Q-007, Q-008, Q-009, Q-010]
correct_items: correct-items.json     # a file next to this one
pass:
  min_caught: 9                       # of the planted errors
  max_false_rejects: 1                # correct items rejected or changed
```

A plan needs at least 10 planted errors and 5 correct items. At most half the correct items may
be allowed as false rejections, so that rejecting everything cannot pass.

**Set the pass mark before anyone sits the session, and write down why.** With few items, a
result is partly luck: of 10 defects, someone who usually catches 90% will catch 8 or fewer
about one time in four. A pass mark of 9 would fail them that often.

## 3. Run it

With your own review screen, an engineer uses the Python API:

```python
sessions = oversight.qualification()
session = sessions.start("r-104", load_plan("plan.yaml"))
# For each item in session.items: show item.payload, then record the answer.
sessions.judged(item.token, "r-104", {"classification": "major"})  # optional, before showing
sessions.shown(item.token, "r-104")                               # optional
sessions.answered(item.token, "r-104", "reject")   # an edit also passes final_payload
result = sessions.finish(session.session_id)
```

Or with files:

```bash
rp qualify start --queue deviation-triage --reviewer r-104 --plan plan.yaml --out sheet.json
rp qualify finish --queue deviation-triage --answers answers.yaml
```

The worksheet holds only a token and the content of each item. The answers file:

```yaml
session_id: the-id-from-the-worksheet
reviewer: r-104
answers:
  - token: the-token-from-the-worksheet
    decision: approve              # approve, reject, or edit with final_payload
```

A reviewer has one unfinished session at a time. To stop early, use `rp qualify abandon`; it
counts as not passed.

## 4. After the session

- **The result is one person's score.** Share it under your reviewer privacy policy, and use it
  for qualification and coaching.
- **A retake needs new planted errors.** ReviewProof refuses ones the reviewer has had. Keep
  a reserve, and use different items for people who can talk to each other.
- **Requalification:** after any change to the AI system's model, prompt or tools, a pass no
  longer counts. Check with `rp qualify status --queue ... --reviewer ...`.
- ReviewProof **does not stop** an unqualified person reviewing. Your application or procedure
  must do that.
- `rp verify --planted planted.db` checks that no session's items or result has been changed.
