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
| `planted/` | Six example planted errors, one per defect class. Public, so never for real use |
| `integrate.py` | The whole flow, wrapped around a plain approval function |

The ID of the record that gets written (the *target ID*) is deliberately not part of the item.
The host passes it to ReviewProof separately and the review screen never shows it
(see [ADR-003](../../docs/adr/0003-api-shape.md)).

## Demo rules

These invented rules are what a demo reviewer is expected to know and check. All 20 sample items
follow them. See the [authoring guide](../../docs/guides/writing-planted-errors.md).

1. Each product exists in one strength, shown in the table.
2. Each product has its own lot number series.
3. Every major or critical deviation needs a CAPA. A minor one does not.
4. A critical deviation's immediate actions include "Placed the batch on hold" and
   "Notified the Quality Unit".

| Product | Lot numbers |
| --- | --- |
| Examplol 50 mg tablets | `LOT-A…` |
| Demozine 10 mg capsules | `LOT-B…` |
| Placebrin 200 mg tablets | `LOT-C…` |
| Mockaril 5 mg tablets | `LOT-D…` |
| Synthavax diluent 2 mL vials | `LOT-E…` |
| Fictamab 100 mg injection | `LOT-F…` |

Each sample item has two or more immediate actions, so an omission defect can remove one while the
item still matches the schema.
