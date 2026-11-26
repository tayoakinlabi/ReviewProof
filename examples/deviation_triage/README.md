# Demo workflow: deviation triage

An AI agent proposes a triage for a manufacturing deviation, and a QA reviewer approves, edits or
rejects it before it is written to the quality system.

**All data here is synthetic.** Products, lots, deviations and findings are invented. They are
demo examples, not classification guidance, and results from this demo are never real-world results.

| File | Contents |
| --- | --- |
| `item.schema.json` | The format of one proposal, as the reviewer sees it |
| `items.json` | 20 correct sample proposals |
| `reviewproof.yaml` | The demo system and queue settings |

The ID of the record that gets written (the *target ID*) is deliberately not part of the item.
The host passes it to ReviewProof separately and the review screen never shows it
(see [ADR-003](../../docs/adr/0003-api-shape.md)).

Each sample item has two or more immediate actions, so an omission defect can remove one while the
item still matches the schema.
