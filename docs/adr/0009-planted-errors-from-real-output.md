# ADR-009: Drafting planted errors from real agent output

- **Status:** Accepted
- **Date:** 2026-11-27

## Context

A planted error that looks unlike the AI's real output can be spotted, and review then looks
better than it is. Items drafted from real output keep its style, length and typical content.

Today the [drafting helpers](../guides/writing-planted-errors.md#drafting-helpers-optional)
make one of three kinds of defect in one item, which must be synthetic or de-identified. They
copy its identifiers, dates and explanations unchanged. [ADR-003](0003-api-shape.md) says the
tool never samples or copies live items.

Two facts shape this design.

1. **What enters `planted.db` cannot be taken out.** On 27 November 2026, on a test database:
   deleting an imported planted error was refused, so was changing its content, and after
   retiring it the content was still in the file. After one had been used, forcing its content
   out made verification fail. Its content is also shown to reviewers, reused, and held in
   backups. **So real information must be removed before import. There is no later.**
2. **An approved item is not proof of a correct one.** Whether approvals can be trusted is what
   the tool tests. A defect added to an item that is already wrong gives it two, and a reviewer
   who finds only the other one is scored as missing.

Already decided: the input is **a file the organisation exports**, **an engineer** runs the
tool from the command line, and **a person chooses the defect**, with rule-based helpers.

## Decision

### A separate drafting tool

A command group, `rp craft`. It **reads only the files it is given**. It opens neither
database, reads nothing from a queue, makes no network connection and uses no AI model.
ReviewProof still stores no content of real items. The tool writes files. Importing and
approving them work as before.

It never changes or deletes the files it reads, and never overwrites a file. **Its messages
never repeat a value taken from the export,** because terminals and logs are kept: they name
the property and the kind of problem.

### Inputs

- **The export:** a JSON list of items, each in the queue's item format and nothing more.
  Anything else in an item is refused.
- **A handling profile** for the queue, kept under change control. **Every property of the item
  format must be listed,** or nothing is made:

  | Handling | For | What the tool does |
  | --- | --- | --- |
  | `replace` | Identifiers, such as record, lot and subject numbers | Needs a new value, typed or made from a pattern such as `DEV-2027-{4 digits}` from a range you have reserved. Replaces the old value everywhere in the item, free text included |
  | `shift_date` | Dates written as `YYYY-MM-DD` | Moves all of them by the same number of days |
  | `keep` | Values that identify no person and no record, such as a classification | Copies it. Listing a property here is your decision that it is safe |
  | `review` | Free text, lists of text and nested objects | Copies it, searches it, and requires a person to read it |

- **A list of blocked terms:** names of people, sites, suppliers, customers, products or
  anything else that must never appear.

### Steps

1. **`rp craft start`** takes one item from the export, applies the profile and writes a
   **working file**. It may still hold real information. `rp plant import` refuses it.
2. **The author adds one defect,** by hand or with `rp craft defect`, which offers the three
   existing helpers, and rewords any text that no longer fits.
3. **`rp craft check`** searches **every text in what would be imported,** including the
   correct value and the notes.

   **These stop the draft from being finished:**
   - any value of a `replace` property from **any** item in the export, because free text can
     mention another record. Matching ignores case, spaces, hyphens and underscores, and the
     different ways the same character can be written. Values under 4 characters are matched as
     whole words;
   - any blocked term;
   - an unshifted date from this item's `shift_date` properties;
   - a change to a `keep`, `replace` or `shift_date` property other than the defect field,
     which would be a second defect;
   - no change in the defect field, a result that does not fit the item format, or the reserved
     target prefix.

   **These are warnings, each of which a person must acknowledge:**
   - text that looks like an email address, telephone number, date or long number;
   - sentences copied unchanged from the original, which may hold details nothing else finds,
     and which a reviewer may remember;
   - a changed classification whose explanation was not reworded.

4. **`rp craft finish`** checks again, and writes the planted-error file only if nothing stops
   it. It requires:
   - an acknowledgment of each warning, **tied to the exact text**, so that it does not carry
     over to a later edit;
   - `--source-confirmed-by`: who confirmed that **the original was correct**, under the
     organisation's procedures, before the defect was added;
   - `--checked-for-real-data-by`: who **read the whole finished item** and found no real
     information.

   **Neither may be the author.** One person may do both. ReviewProof cannot verify who is
   typing.

The file then goes through the usual import and approval. Approvers also confirm that they
found no real information in it.

### Where it came from is recorded

A finished file has a `provenance` entry: that it came from real output, who confirmed the
source, who checked for real data, when, and the tool's version. `planted.db` keeps it in a new
column (**`planted.db` schema version 4**), which cannot change. For a planted error that has
one, the fingerprint the log commits to includes it. Others keep their fingerprint, so existing
logs still verify. The pack's register lists it, so QA can find every planted error drawn from
real output.

**Nothing that identifies the original is kept:** no record number and no hash of it, which
would tie the planted error to a real record.

### What the tool cannot do

**It cannot make an item anonymous, and must not be described as doing so.**

- It finds identifiers listed in the profile and terms on your list. **It cannot find a name,
  place or detail that nobody told it about.** Reading the whole item is the real check.
- Removing identifiers may leave details that point to a person or an event. Such data may
  still be personal or confidential data under your rules or the law.
- It cannot tell that the original was correct, or that a new identifier matches no real record.
- It cannot know who saw the original. A reviewer may recognise it.
- A file written by hand, or edited after finishing, carries only what its writer puts in it.
  Nothing forces anyone to use this tool.

## Alternatives not chosen

- **Capture items from the live queue.** ReviewProof would have to store real content, which it
  now never does, and real data would flow into it with no person deciding.
- **Let an AI model suggest or write defects.** It could add variety, but is another system to
  assess, may send real data outside the organisation, and may favour defects that are easier
  or harder to find than real ones. It can be reconsidered.
- **A screen for QA staff.** An engineer will run it. The working file is plain text that QA
  staff can read.
- **Record where a draft came from in its notes.** No database upgrade, but free text that
  cannot be reliably found or checked.
- **Rely on warnings alone.** What cannot be taken out later has to be stopped.

## Consequences

- **Real data is handled while drafting.** The export and working files hold it. Your
  organisation must decide whether this use is allowed, protect the files and destroy them.
- Another upgrade of `planted.db`.
- Each draft needs the author and at least one other person before approval.
- Rule-based helpers make value swaps, omissions and class changes easily. Unsupported claims,
  wrong targets and policy breaches still need writing by hand. **If the easy kinds dominate,
  the catch rate measures the wrong thing:** the procedure should require a planned mix.
- This changes part of ADR-003. In the validation pack, **REQ-005 changes**, because an
  exported item may be one that was submitted for review. It will say that ReviewProof never
  reads a queue or its databases to make a planted error, and drafts only from files the user
  supplies. New requirements cover the checks above. DEP-012 allows de-identified real output
  as a source. A new deployment duty covers permission to use it, a privacy assessment, which
  outputs may be used (for example their age and who reviewed them), and control and
  destruction of the files. A new risk, **real information enters `planted.db` and cannot be
  removed**, proposed High. The writing guide and planted-error procedure change.
- It is built and tested with synthetic data only.

## Not in the first version

AI suggestions, a graphical screen, reading a queue, finding unlisted names automatically,
dates in other formats, and handling inside nested objects.

## Checks before accepting an implementation

- A property missing from the profile stops everything.
- Each listed identifier from any exported item, in each spelling the matching covers, and each
  blocked term, is found wherever it is put, the correct value and notes included.
- A second structured change is refused. An acknowledgment stops counting when its text
  changes. A working file cannot be imported.
- The tool opens neither database, makes no network connection, never prints a value from the
  export, and never changes, deletes or overwrites a file.
- Databases from release 0.1.0 and from today's code upgrade with every row unchanged and logs
  still verifying.
- **A test set of invented "real" details records what the search misses,** and the
  documentation says so.
- Breaking each safeguard makes a test fail.

## How the implementation was checked

- Automated tests cover the profile and export, each step, each thing that stops a draft, the
  warnings and acknowledgments, both checks, the commands, provenance and the upgrades. One
  list of invented details records spellings that are found, and another what is missed. The
  [authoring guide](../guides/writing-planted-errors.md#what-the-search-finds-and-what-it-misses)
  shows the misses.
- On 27 November 2026, on the demo's synthetic items, a draft was taken through the commands to
  import, two approvals, verification and the register. Importing its working file was refused.
- The same day, a database made by release 0.1.0, and one made by the repository's code before
  this record (schema versions 4 and 3, with a void in effect), were upgraded. Every earlier
  value was unchanged, and each log verified against the head the earlier code had exported.
  This was run by hand.
- Breaking each of 60 safeguards in turn made a test fail. The first run found safeguards that
  no test covered alone, because a second check hid their loss. The tests were corrected.
- Where the implementation goes further than the decision above:
  - the search also ignores accents, invisible characters and every kind of dash;
  - warnings cover the defect field whatever its handling, as well as free text, the correct
    value and the notes, because a wrong value its author types may be a real one;
  - the names given for the two checks are searched too;
  - after an unexpected failure, a command shows only the kind of failure.
- **Not checked:** use with real data (only synthetic data was used), whether a real team can
  keep a handling profile complete and a mix of defects, real item formats with nested
  objects, older SQLite versions and large exports.

