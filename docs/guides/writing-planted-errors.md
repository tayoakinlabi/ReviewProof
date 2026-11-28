# Writing planted errors

This guide is for the QA staff who write and approve planted errors, and for the engineer who
loads them.

> **Status:** ReviewProof is pre-release and not pre-validated. An engineer or QA
> administrator runs the [`rp plant` commands](../cli.md), or the short Python examples
> below. They do the same things.

## What a planted error is

A planted error is an item **you write**, in your queue's own item format, with **one known
defect**. Reviewers are meant to see it mixed in with real work and to reject it or correct it.
It must never be executed or enter a GxP record.

How often reviewers catch planted errors is what ReviewProof measures. A planted error that is
easy to spot **because it looks fake** makes reviewers seem better than they are. One that nobody
could catch makes them seem worse. Both give you a wrong answer, so the writing matters.

## Three rules

1. **Keep them secret.** Reviewers are told the program exists, never which items are planted.
   Keep your files and `planted.db` where reviewers cannot read them, and never in a shared or
   public repository.
2. **No real data.** Use synthetic or de-identified values, modelled on what real items look like.
   Do not paste a real item into a file. ReviewProof stores planted errors in full, cannot tell
   whether a value is real, and **cannot take one out of `planted.db` once imported.** If your
   organisation allows you to start from real output, use
   [`rp craft`](#drafting-from-real-output-rp-craft), which de-identifies it before import.
3. **Never use the public examples.** The files under
   [`examples/`](../../examples/deviation_triage/planted/) are published. Anyone can read them, and
   a lightly edited copy is no safer.

## What makes a good one

| Quality | Ask yourself |
| --- | --- |
| **One defect** | If I fix this one thing, is the item completely correct? |
| **Detectable** | Can a careful reviewer find it from what is on screen, or from a record they are expected to check? If they would need information they never have, a miss tells you nothing. |
| **Clear answer** | Would two senior reviewers agree it is wrong, and on what to do? If they might argue, do not use it. |
| **Matters** | Would letting this through have a real consequence? A typing slip is not a useful test. |
| **Looks real** | Could this have come from your AI system on an ordinary day? |

Write a **mix**: all the defect classes that can happen in your workflow, some easier and some
harder. Omissions are often the hardest to see, so do not leave them out.

### Making it look real

- **Fix what gives it away.** If you change a classification, reword the rationale to argue for
  the wrong class as convincingly as your AI would. A rationale that still argues for the right
  class is a clue real proposals would not give. Check other fields that follow from the one you
  changed.
- **Use identifiers that look normal but are not taken.** A record number, lot or subject ID must
  fit your usual format and must **not** match a real one, now or later. ReviewProof cannot check
  this. Agree with whoever runs your numbering how to avoid collisions, without creating a GxP
  record for a planted item.
- **Keep dates believable.** An item dated months before the rest of the queue stands out. Planted
  errors with dates go stale, so replace them regularly.
- **Do not reuse what reviewers have seen.** A near-copy of last week's deviation invites
  comparison.
- **Do not worry about field order.** ReviewProof puts the fields of every item, real or
  planted, in the order your item format lists them. Do write numbers and lists the way your
  system does, including the order of list entries.
- **Match the style.** Use the length, tone and habits of your system's real output. If you write
  noticeably better or worse than it does, reviewers will learn the difference.
- **Put everything the reviewer needs in the item.** A planted item has no agent trace,
  sources or record history. If the review screen shows such things for real items, planted
  ones stand out.
- **Never put a target ID in the item.** The ID of the record to be written is not part of an
  item. ReviewProof gives each planted error its own ID from a reserved range, and refuses any
  item containing that range's prefix.

## The file

One YAML or JSON file per planted error.

| Key | Required | Meaning |
| --- | --- | --- |
| `planted_error_id` | Yes | Your ID: 1 to 64 letters, digits, `.`, `_` or `-` |
| `queue` | Yes | A queue named in your settings file |
| `defect_class` | Yes | `wrong_value`, `omission`, `wrong_classification`, `unsupported_claim`, `wrong_target` or `policy_breach` |
| `defect_field` | Yes | The top-level property of your item format where the defect is |
| `expected_decision` | Yes | `reject`, or `edit:` followed by the `defect_field` |
| `author` | Yes | Who wrote it |
| `payload` | Yes | The whole defective item, exactly as a reviewer will see it |
| `correct_value` | No | What the defect field should have said. Recommended: it helps approvers, and ReviewProof checks that it really differs. |
| `notes` | No | Anything approvers should know |
| `purpose` | No | `live` (the default) or `qualification`. Qualification ones are used only in [qualification sessions](qualification.md), never in a live queue. |

In YAML, **put dates, times and version numbers in quotes**. Without quotes, `2026-01-15` is read
as a date object, not as text, and `1.10` becomes the number 1.1.

### How a decision will be scored

| The reviewer | Result |
| --- | --- |
| Rejects the item | Catch |
| Edits it, and the edit changes the `defect_field` (ReviewProof checks the change itself; it does not check that the new value is right) | Catch |
| Edits it, but leaves the `defect_field` alone | Miss |
| Approves it | Miss |

Choose the `defect_field` with this in mind. If a reviewer could reasonably fix the problem by
editing a different field, set `expected_decision: reject`, tighten the item so that there is one
obvious fix, or choose another defect.

An omission **must still match your item format**. Remove something the format does not require,
such as one entry in a list. An item with a missing required field could never come from the real
system, so it would stand out.

## One example of each class

All six use the invented [deviation triage demo](../../examples/deviation_triage/README.md), which
lists the demo's own rules. Those rules are what make the defects detectable. Your equivalents are
your procedures, product records and specifications.

Each snippet shows the part of the file that matters.

### Wrong value

A dose, date, lot or subject ID is swapped. Here the product strength is wrong
([full file](../../examples/deviation_triage/planted/wrong_value.yaml)).

```yaml
defect_class: wrong_value
defect_field: product
expected_decision: edit:product
correct_value: Demozine 10 mg capsules
payload:
  product: Demozine 100 mg capsules
```

*Why it works:* the product list gives one strength. A tenfold strength error matters.

### Omission

Something required is missing. Here a critical deviation does not notify the Quality Unit
([full file](../../examples/deviation_triage/planted/omission.yaml)).

```yaml
defect_class: omission
defect_field: immediate_actions
expected_decision: edit:immediate_actions
payload:
  immediate_actions:
  - Placed the batch on hold
  - Quarantined the filter assembly
```

*Why it works:* the list still looks complete. The reviewer has to know what should be there.

### Wrong classification

Here a breach of aseptic technique is proposed as major instead of critical
([full file](../../examples/deviation_triage/planted/wrong_classification.yaml)).

```yaml
defect_class: wrong_classification
defect_field: classification
expected_decision: edit:classification
correct_value: critical
payload:
  classification: major
```

*Why it works:* the rationale was reworded to sound plausible, so approving it takes no effort
and catching it takes judgement.

### Unsupported claim

The item states something that nothing supports. Here a root cause is declared while
identification is still pending
([full file](../../examples/deviation_triage/planted/unsupported_claim.yaml)).

```yaml
defect_class: unsupported_claim
defect_field: root_cause
expected_decision: edit:root_cause
payload:
  root_cause: The plate was contaminated by contact during removal; the organism is a common skin bacterium.
```

*Why it works:* it is confident and specific, and contradicted only by an action further down.

### Wrong target

The item points at the wrong thing: the wrong lot, subject, product or record. Here the lot
belongs to another product
([full file](../../examples/deviation_triage/planted/wrong_target.yaml)).

```yaml
defect_class: wrong_target
defect_field: lot_number
expected_decision: reject
correct_value: LOT-A10291
payload:
  lot_number: LOT-D40291
```

*Why it works:* the number is correctly formatted. Only checking it against the product exposes
it. The expected decision is reject, because the reviewer should not have to guess the right lot.

### Policy breach

The proposal breaks a procedure. Here a major deviation raises no CAPA
([full file](../../examples/deviation_triage/planted/policy_breach.yaml)).

```yaml
defect_class: policy_breach
defect_field: capa_required
expected_decision: edit:capa_required
correct_value: true
payload:
  capa_required: false
```

*Why it works:* one short field, easy to skim past, with a clear rule behind it.

## Drafting helpers (optional)

For three kinds of defect, a helper can start a file from a **correct** item that you supply. The
source must itself be synthetic or de-identified. A helper changes exactly one field and writes a
file with a review checklist. It does not import, approve or use anything.

| Helper | What it does |
| --- | --- |
| `draft_value_swap` | Replaces a value with the `new_value` you give |
| `draft_omission` | Removes one list entry (`index`), or a property your format allows to be absent |
| `draft_classification_flip` | Changes to another allowed class. By default, the one listed before the current one in your item format, so list classes from least to most severe. |

```python
import json

from reviewproof.config import load_config
from reviewproof.mutators import draft_classification_flip, write_draft

config = load_config("examples/deviation_triage/reviewproof.yaml")
queue = config.queues["deviation-triage"]
with open("examples/deviation_triage/items.json", encoding="utf-8") as file:
    source = json.load(file)[2]  # a correct item, classified critical

draft = draft_classification_flip(
    queue, source, planted_error_id="PE-0007", author="your-id", field="classification"
)
write_draft(draft, "PE-0007.yaml")  # never overwrites an existing file
```

**A draft is not ready to use.** It is a copy of its source, with the same identifiers, dates and
rationale. Work through the checklist at the top of the file before importing it.

## Drafting from real output (`rp craft`)

Items drafted from what your AI system really produced keep its style, length and typical
content. [`rp craft`](../cli.md#rp-craft-start) turns an item **you export** into a planted-error
file: it replaces identifiers, shifts dates, searches what you are about to import for real
data, and records who checked it. A person still chooses the defect.

> **It cannot make an item anonymous.** It finds what your handling profile and your list of
> blocked terms tell it to find, and nothing else. **What enters `planted.db` cannot be taken
> out:** deleting it is refused, retiring it leaves its content in the file, and removing it by
> force after use breaks verification. It is also shown to reviewers and held in backups. So a
> person must read the whole item before it is imported.

### Before you start

- **Permission.** Your organisation must have decided that real output may be used this way, and
  how the files are protected and destroyed. Removing identifiers can leave details that point
  to a person or an event, and such data may still be personal or confidential.
- **Which outputs may be used.** For example, how old they must be, and that nobody who reviews
  on the queue worked on them. The tool cannot know who saw an original.
- **Only correct originals.** An approved item is not proof of a correct one: whether approvals
  can be trusted is what you are testing. Someone other than the author must confirm that the
  original was correct. A defect added to a wrong item gives it two.
- **Where you work.** Run the tool where reviewers cannot see, and keep exports, profiles that
  list names, and working files out of shared folders and version control.

### The export

A `.json` file holding a list of items, each in your queue's item format **and nothing more**.
Export broadly, for example every item from a period, not only the one you will use: a value
that is replaced in any exported item is searched for in your draft, because free text often
mentions another record.

### The handling profile

One per queue, kept under change control. **Every property of the item format must be listed,**
or nothing is made. The [demo's profile](../../examples/deviation_triage/handling.yaml):

```yml
queue: deviation-triage
properties:
  deviation_id: {handling: replace, pattern: "DEV-2027-{4 digits}"}
  occurred_on: shift_date
  product: keep
  lot_number: replace
  process_step: keep
  description: review
  classification: keep
  classification_rationale: review
  root_cause: review
  immediate_actions: review
  capa_required: keep
blocked_terms:
  - Example Site North
  - Invented Supplier Ltd
```

| Handling | Use it for | What happens |
| --- | --- | --- |
| `replace` | Identifiers: record, lot, subject and similar numbers | You give a new value with `--set NAME=VALUE`, or a `pattern` makes one with `{N digits}` and `{N letters}`. The old value is replaced everywhere in the item, free text included |
| `shift_date` | Dates written `YYYY-MM-DD` | All move by `--shift-days`. The same dates written that way in text move too |
| `keep` | Values that identify no person and no record | Copied. **Listing a property here is your decision that it is safe** |
| `review` | Free text, lists and nested objects | Copied, searched, and warned about. **Read it** |

`blocked_terms` lists what must never appear: names of people, sites, suppliers, customers,
products or anything else. **The tool cannot guess them.** If there are none, write `[]`.

Use patterns and new values from a range your numbering will never issue. ReviewProof cannot
check that. Choose a value that fits your rules: a lot number from another product's series,
say, would be a second defect.

### The steps

```bash
rp craft start  --profile handling.yaml --export export.json --item 12 \
  --id PE-0014 --author YOUR-ID --set lot_number=NEW-NUMBER --shift-days 211 --out work.yaml
rp craft defect --profile handling.yaml --export export.json --work work.yaml \
  --mutator classification-flip --field classification --to major --out work-2.yaml
rp craft check  --profile handling.yaml --export export.json --work work-2.yaml
rp craft finish --profile handling.yaml --export export.json --work work-2.yaml \
  --source-confirmed-by ID --checked-for-real-data-by ID \
  --acknowledge CODE --acknowledge CODE --out PE-0014.yaml
```

1. **`start`** writes a **working file**. It may still hold real information, and it cannot be
   imported. Never type a real value into a command: terminals keep what you type.
2. **Add exactly one defect,** with `rp craft defect` (value swap, omission or class change) or
   by editing the working file. Then [make it look real](#making-it-look-real): reword what no
   longer fits, and rewrite free text in your own words where it describes a particular event.
3. **`check`** lists what stops the draft and what needs your attention. It names properties
   and kinds of problem, **never the value it found.**

   | Stops it | Needs an acknowledgment |
   | --- | --- |
   | A `replace` value from any exported item | Text that looks like an email address, telephone number, date or long number |
   | A blocked term | Sentences copied unchanged from the original |
   | An original date from a `shift_date` property of this item | A changed classification whose explanation was not reworded |
   | A change to a `replace`, `shift_date` or `keep` property other than the defect | |
   | No change in the defect field, or anything import would refuse | |

   The search covers the whole item, `correct_value`, `notes`, the ID and the author. The
   warnings cover `review` properties, the defect field, `correct_value` and `notes`.

4. **`finish`** writes the planted-error file. Each warning must be acknowledged by its code,
   which covers the exact text: if you edit the text, read it again. Two checks must name
   someone other than the author, and one person may do both:
   - `--source-confirmed-by`: confirmed that **the original was correct** under your
     procedures;
   - `--checked-for-real-data-by`: **read the whole finished item** and found no real
     information.

   ReviewProof cannot verify who is typing. Keep both signatures in your quality system.

Then [import and approve it](#loading-and-approval). Approvers also confirm that they find no
real information. **Destroy the export and working files** as your procedure says: the tool
never changes or deletes them.

### What the search finds, and what it misses

It ignores capital letters, spaces, hyphens and dashes, underscores, line breaks, accents,
invisible characters and full-width forms. So `LOT-C66554` is also found as `lot c66554`,
`LOTC66554` or inside a longer word. A value of fewer than four characters must stand as a
whole word.

**It does not find** these, among others. Each is in the tool's own tests as a miss:

| You listed | Written as |
| --- | --- |
| `LOT-C66554` | `batch C66554` (part of it), `LOT.C66554` or `LOT/C66554`, `LOT-C66545` (a typing mistake), a look-alike letter from another alphabet, or split between two properties |
| `Marta Quillfeather` | `Quillfeather`, `M. Quillfeather`, or a misspelling. List surnames too |
| A short word such as `Ose` | Inside another word |
| Nothing | Any name, place or detail nobody listed |

It also cannot see meaning: "the night shift lead on line 3" may point to one person without
naming anyone.

### What is recorded

A finished file has a `provenance` entry: that it came from real output, who confirmed the
source, who checked for real data, when, and the tool's version. It cannot be changed after
import, and the log's fingerprint of the planted error covers it. `rp plant list` marks such
planted errors `[from real output]`, and the validation pack's
[register](../../validation-pack/sop-planted-errors.md#9-realism-rotation-and-changes) lists
them with both names. **Nothing that identifies the original is kept,** so keep no link to it
elsewhere either unless your rules require one.

A file written by hand, or edited after `finish`, carries only what its writer puts in it.
Nothing forces anyone to use this tool.

### Keep a mix

The helpers make value swaps, omissions and class changes easily. Unsupported claims, wrong
targets and policy breaches need writing by hand. **If only the easy kinds get written, the
catch rate measures the wrong thing.** Plan the mix before drafting.

## Loading and approval

From a terminal:

```bash
rp plant import my-planted-error.yaml
rp plant approve PE-0007 --approver qa-lead-1
rp plant list
```

Or in Python:

```python
from reviewproof.config import load_config
from reviewproof.planted import PlantedLibrary
from reviewproof.store import create_planted_db

config = load_config("examples/deviation_triage/reviewproof.yaml")
database = create_planted_db("planted.db")
library = PlantedLibrary(database, config)

library.import_file("examples/deviation_triage/planted/wrong_value.yaml")
library.approve("DEMO-WV-1", approver="qa-lead-1")
library.approve("DEMO-WV-1", approver="qa-lead-2")  # a GxP queue needs two people

for planted in library.all():
    print(planted.planted_error_id, library.status(planted).value)
```

**Import refuses**, and says why: an item that does not match the queue's format; a repeated or
unknown key; a `correct_value` equal to the "defective" value; an item containing the reserved
target prefix; the same item as another planted error still in use in that queue; and an ID that
already exists. To correct an imported planted error, retire it and import the new version with a
**new ID**.

Once a planted error has been used, **its approvals are locked**. If a queue later becomes
GxP and a used planted error has only one approval, retire it and import it again under a
new ID.

**Approval:** every planted error needs approval from someone other than its author. A queue
marked `gxp: true`, which is the default, needs two different approvers. Nothing is used until it
has the approvals its queue requires.

**What approval does not prove.** ReviewProof has no sign-in. It checks that the names differ, but
**cannot prove who typed them**. Control who can write to `planted.db` with file permissions, and
keep your formal sign-off in your quality management system.

### What an approver checks

- [ ] The item is truly defective, in the way the file says.
- [ ] There is exactly one defect.
- [ ] A careful reviewer could find it with what they have.
- [ ] The expected decision is what your procedures require.
- [ ] It looks like a real item, and nothing marks it out.
- [ ] Its identifiers cannot match a real record.
- [ ] It holds no real data.

## Looking after the library

**Write enough.** ReviewProof uses a planted error more than once. It takes one of the least
used, and never puts a second copy in a queue while one is waiting for a decision. If your
system says who will review an item, it also skips planted errors that person has already
met. If it cannot say, as is common with a shared queue, **someone may meet the same one
twice**, and colleagues may tell each other about it. Only a reviewer's first sight of a
planted error counts towards scores, so repeats add nothing to what you learn.

Plan for what one reviewer meets. At a 2% rate, someone reviewing 100 items a week meets about
two a week, or about 16 over eight weeks. The actual number is random and may well be higher or
lower. No individual score is shown below 10 planted errors. Treat 16 different planted errors as
a minimum for that reviewer and period, and keep a reserve.

If none can be used when one is due, the queue **runs short**. ReviewProof records each time, so
check that count and add planted errors when it rises.

**Feedback.** Reviewers can be told the outcome of each planted error they handled, at once
or in batches, as set for the queue. Feedback shows them the item. Because planted errors
are reused, someone could describe it to colleagues, so consider batched feedback and retire
items that become talked about.

**Check realism.** From time to time, ask reviewers which items they suspected were planted. If
they can pick one out, retire it. Do this without confirming which of their guesses were right.

**Rotate.** Replace planted errors as they age, as reviewers see them, and whenever your AI
system's output changes in style.

**Review after changes.** If your item format changes, planted errors that no longer match it are
held back until replaced. `stale()` lists them and says why. If your procedures change, check that
each expected decision is still right: **ReviewProof cannot detect that for you.**

Retiring is permanent, and records who did it and why. **It does not remove the planted
error's past results from any score.** If one turns out to be faulty, for example the item was
in fact correct, [void it](../cli.md#rp-plant-void): that takes two people, removes all its
results, catches and misses alike, and every report says what it removed. The validation pack's
[procedure](../../validation-pack/sop-planted-errors.md#10-a-planted-error-that-is-wrong-or-recognisable)
has the steps.

```python
library.retire("DEMO-WV-1", retired_by="qa-lead-1", reason="a reviewer suspected it")
print(library.stale("deviation-triage"))  # approved, but no longer matching the item format
database.close()
```
