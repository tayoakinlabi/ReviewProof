# ADR-007: VITAL risk ratings

- **Status:** Accepted
- **Date:** 2026-11-27

## Context

A queue's settings take a risk rating from the VITAL framework
([arXiv version 1](https://arxiv.org/abs/2611.22973v1), a preprint). ReviewProof uses it to
choose the **default planted share**, which `planted_rate` can replace, and copies it into the
`queues` table. Nothing reads it back from the database.

The settings do not follow VITAL's scales:

| VITAL version 1 | ReviewProof before this record |
| --- | --- |
| Base risk A1 to A10, and a half step where a use spans two neighbours, such as 5.5 | Whole numbers only. Half steps are refused. `A11` to `A99` are accepted |
| Adjusted category Low, Moderate, High, Very High or Critical | `low`, `medium` or `high` only |

So a queue rated Very High or Critical had to be entered as something it was not.

VITAL's adjusted score multiplies the base risk by factors for reversibility, transparency and
patient impact. It also sets floors: a use with direct patient care or safety-critical action is
**Very High or above whatever its base risk**, and Critical if its base risk is also 7 or more.
It treats Critical as outside its routine validation route. VITAL sets no planted share. The shares are
ReviewProof's own untried proposals: 1% for A1 to A4, 2% for A5 and A6, 3% from A7, and one
step higher for High.

## Decision

### Settings

| Setting | Accepted | Stored as |
| --- | --- | --- |
| `vital_base_risk` | `A1` to `A10`, and `A1.5` to `A9.5` | As written |
| `vital_adjusted_category` | `low` | `low` |
| | `moderate`, and `medium` as before | `medium` |
| | `high` | `high` |
| | `very_high` | `very_high` |
| | `critical` | `critical` |

`A11` and above are **refused**, because they are not on VITAL's ladder. Before, a typing
slip such as `A55` loaded without complaint.

`moderate` is VITAL's word. It is stored as `medium` so that no stored value, existing settings
file or use of `RiskCategory.MEDIUM` changes. `RiskCategory` gains `VERY_HIGH`, `CRITICAL`,
and `MODERATE` as another name for `MEDIUM`.

### Default planted share

- **A half step takes the next whole step's share.** This matters only for `A4.5` (2%) and
  `A6.5` (3%).
- **High still raises the share by one step.**
- **Very High and Critical take the top share, 3%.**
- No rating from `A1` to `A10` that was accepted before gets a different share.
  `planted_rate` still replaces the default.

| Base risk | No category, Low or Moderate | High | Very High or Critical |
| --- | --- | --- | --- |
| A1 to A4 | 1% | 2% | 3% |
| A4.5 to A6 | 2% | 3% | 3% |
| A6.5 and above | 3% | 3% | 3% |

Only the first row differs between this and treating Very High like High. It matters because
VITAL's patient floor can make a queue with a low base risk Very High.

ReviewProof does nothing else with Critical. Accepting the word records a rating. It does not
say that ReviewProof, or the use, is suitable at that level.

### Database: `oversight.db` schema version 3

Only the categories need it: the base risk column has no constraint. SQLite cannot widen a
check constraint, so version 3, in one transaction:

1. renames `queues.vital_adjusted_category` to `vital_adjusted_category_v2`;
2. adds `vital_adjusted_category` with the five stored values;
3. copies every value across, and checks that none differs.

The old column stays, unused. `planted.db` stays at version 2.

**Version 2 is not edited.** It is not in any release, but a database created or upgraded from
the repository's source is already at version 2 and would never run an edited version 2.

The rules in [ADR-006](0006-qualification.md#database-upgrade-schema-version-2) apply: only
`rp init` or `initialise(..., upgrade=True)` upgrades; both databases are backed up first;
`rp verify` and `rp report` still read an older database; earlier releases refuse an upgraded
one. A database from 0.1.0 goes to version 3 in the same single upgrade.

### What was observed

Five ways were tried on 27 November 2026, on copies of a version 2 database with 37 items, a
qualification row and 110 log events, using SQLite 3.50.4. `items` and `qualifications` have
foreign keys to `queues`.

| Way | Result |
| --- | --- |
| Rebuild `queues` as a new table | Fails on the foreign keys. Rolled back |
| The same, with foreign key checks deferred | Fails the same way. Rolled back |
| Rebuild with foreign keys switched off, then check them | Works |
| **Replace the column, keep the old one** | **Works** |
| Replace the column, drop the old one | Works |

For the three that worked: the row counts and the stored category were unchanged, an unknown
category was refused, the foreign keys still held, SQLite's integrity check passed, and the log
verified with the same head. Editing version 2 was also tried: a database already at version 2
still refused `very_high`.

Older versions of SQLite were not tried. By SQLite's release history, renaming a column needs
3.25 or later and dropping one 3.35 or later.

## Alternatives not chosen

- **Very High and Critical raise the share by one step, like High.** Simpler to explain, but a
  queue VITAL rates more severe would get no more planted errors than a High one, and as few as
  2%.
- **Rebuild with foreign keys off.** It leaves no unused column, but changes the code that runs
  every upgrade, which protects the evidence, to switch a protection off. Foreign keys cannot
  be switched inside a transaction, so this must happen around the whole upgrade.
- **Drop the old column.** It leaves no unused column, but needs a newer SQLite. Some systems
  with long support lives ship old versions.
- **No database change:** store `high` for Very High and Critical, or nothing. The database
  would then hold a rating that is false or missing.
- **Keep the settings as they were** and rely on advice in the
  [VITAL mapping](../../validation-pack/vital-mapping.md#recording-your-vital-risk-rating).

## Consequences

- A queue with a base risk of A1 to A4 rated Very High or Critical needs about half as many
  planted errors again as a High one.
- Anyone with a version 2 database must back up and run `rp init` again before the application
  will start.
- A settings file with `A11` or above stops loading.
- A validated user would retest the upgrade, and check each queue's planted share.
- `queues` keeps an unused column.
- The validation pack's rating advice and the two procedures that quote the shares change.
- **Not included:** ratings still appear in no report and no log entry, and verification does
  not cover the `queues` table. The settings file, under change control, remains the record.
  ReviewProof does not work out VITAL's score, and a change of rating flags no one for
  requalification.

## How the implementation was checked

- A test gives every base risk with every category its share in the table above. Others check
  that values not on VITAL's scales are refused.
- On 27 November 2026, two databases made by earlier code were upgraded: one by the released
  0.1.0 package, and one by the repository's code at schema version 2 (commit `138ee2b`). Each
  held reviews and the rating `A6`, `medium`. Every row and earlier value of both files was
  unchanged. The upgraded files passed SQLite's integrity and foreign key checks, and the log
  verified against the head the earlier code had exported. A review with `A6.5` and `critical`
  was then recorded. Each earlier version refused its upgraded database. This was run by hand:
  the automated tests make their earlier databases with the current code.
- Automated tests check that a rating copied wrongly, or a statement that fails, leaves the
  database as it was, including the version 2 changes when starting from version 1, and that
  nothing upgrades without being asked.
- Breaking each of 11 rules and safeguards in turn made a test fail.
