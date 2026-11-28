# 21 CFR Part 11 control table

**Template.** For the validation lead and quality unit. Read the [overview](README.md) and
complete the [intended-use statement](intended-use.md) first. Text marked **[FILL]** is yours
to write.

> **ReviewProof does not make a system comply with Part 11.** It has no sign-in, access control
> or electronic signatures. This table says what the software does toward some controls, and
> what is left to you. No one independent of the authors has reviewed it. It is not legal or
> regulatory advice.

The clauses are summarised from the text in the
[Electronic Code of Federal Regulations](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11),
as it stood on 24 November 2026. The summaries are short and leave things out: **read the
regulation.**

## 1. Does Part 11 apply to ReviewProof's records?

Decide this first. FDA's guidance
[Part 11, Electronic Records; Electronic Signatures: Scope and Application](https://www.fda.gov/media/75414/download)
(August 2003) says FDA interprets the scope narrowly: broadly, records that other FDA rules
(the *predicate rules*) require you to keep, when you keep them electronically in place of
paper or rely on them electronically; certain records submitted to FDA; and electronic
signatures meant to equal handwritten ones. It recommends that you decide for each record, and
write the decision down. The [records ReviewProof keeps](intended-use.md#5-records) are:

| Record | A Part 11 record in your use? Which predicate rule, and why |
| --- | --- |
| Event log | **[FILL]** |
| Item registry | **[FILL]** |
| Planted errors, with approvals and retirement | **[FILL]** |
| Qualification sessions and results | **[FILL]** |
| Reports, exported log heads and worksheets | **[FILL]** |

**Enforcement discretion.** The same guidance says that, while FDA re-examines Part 11, it does
not intend to take enforcement action on the Part 11 requirements for validation, audit trails,
record retention and record copying, **as the guidance explains**: § 11.10(a), (b), (c), (e)
and (k)(2), and the corresponding parts of § 11.30. These are marked † below. Part 11 remains
in effect, the predicate rules still apply, including their own requirements for these things,
and FDA says it intends to enforce the other provisions. **Confirm that the guidance is still
current** when you rely on it. For clinical investigations, also read FDA's later guidance,
*Electronic Systems, Electronic Records, and Electronic Signatures in Clinical Investigations:
Questions and Answers* ([notice of availability](https://www.federalregister.gov/documents/2024/10/02/2024-22562/electronic-systems-electronic-records-and-electronic-signatures-in-clinical-investigations-questions),
October 2024).

## 2. Controls for closed systems, § 11.10

"Closed" means that the people responsible for the records control access to the system. The
`REQ-` and `DEP-` numbers refer to the [requirements](requirements.md).

| Clause | In short | ReviewProof | Not provided | Your control, and how you meet it |
| --- | --- | --- | --- | --- |
| (a) † | Validate for accuracy, reliability and consistent performance, and to discern invalid or altered records | Verification reports changed, removed or reordered log events, and later changes to what the log committed to (REQ-026, REQ-027, REQ-033). The project's tests and this pack are inputs to your validation | It is not pre-validated. Without a head exported earlier, removal of the latest events or a rebuilt log goes unnoticed. Verification does not cover every stored record ([section 3](#3-the-audit-trail--1110e)) | DEP-010, DEP-018. **[FILL]** |
| (b) † | Make accurate and complete copies, readable by people and in electronic form | Reports as HTML or CSV; an exported log head; a [CSV register](planted_records.py) of planted errors. Records are held in two SQLite database files | **No command exports the log or the records completely in readable form.** A report is a summary. The content of real items is not stored, and no command shows afterwards that a document you hold is the one reviewed | Decide how you produce copies, and keep reviewed content in your system of record. **[FILL]** |
| (c) † | Protect records for accurate, ready retrieval throughout the retention period | The databases refuse changes to and deletion of log events and key records. An upgrade copies the log exactly or is undone (REQ-024, REQ-046) | No backup, archive or retention feature. Someone with write access to the files can remove the protections or the files. Before version 1.0.0 the database format may change | DEP-007, DEP-011, and a plan for reading old records later. **[FILL]** |
| (d) | Limit access to authorised people | Nothing | No sign-in, accounts or roles | DEP-007, DEP-008. **[FILL]** |
| (e) † | Secure, computer-generated, time-stamped audit trails of actions that create, change or delete records; changes must not hide earlier information | See [section 3](#3-the-audit-trail--1110e) | **It is not a trail of every action**, and it takes the operator's ID and the time from your system | DEP-008, DEP-009, DEP-010. **[FILL]** |
| (f) | Operational checks that enforce the permitted order of steps, as appropriate | One decision, only after that reviewer was shown the item; a write only after a recorded approval or edit of that content, requested at most once; pass criteria recorded before a qualification session (REQ-014, REQ-016, REQ-020, REQ-031) | Only these steps, and only for writes that go through its guard. "Shown" is what your application reports | DEP-001. **[FILL]** |
| (g) | Authority checks: only authorised people use the system, sign, alter records or operate | It requires approvers' names to differ from the author's, and a session to be answered under its reviewer's ID (REQ-003) | **No authority check.** It cannot tell whether a name is true or its owner authorised | DEP-007, DEP-008, DEP-014. **[FILL]** |
| (h) | Device checks, as appropriate, on the source of input or instructions | Nothing | All of it | **[FILL]** |
| (i) | People who develop, maintain or use the system have the education, training and experience for their tasks | Nothing about training. A qualification session measures one reviewing skill | Training records. No statement about the developers' qualifications | Your training system, your supplier assessment, and the roles in the [procedures](README.md#what-is-here). **[FILL]** |
| (j) | Written policies that hold people accountable for actions under their electronic signatures | Nothing: it has no electronic signatures | All of it, if you use signatures | **[FILL]** |
| (k)(1) | Control who gets, accesses and uses system documentation | The project's documents are in its public repository | Control of your copies and your own documents | Your document control. **[FILL]** |
| (k)(2) † | Change control for system documentation, with a time-ordered trail | The repository's history and the [changelog](../CHANGELOG.md) record the project's changes | Change control of your documents and settings | DEP-015, DEP-018. **[FILL]** |

## 3. The audit trail, § 11.10(e)

The **event log** is hash-chained: each event includes a hash of the one before. The database
refuses to update, delete or replace an event (REQ-024). Someone with write access to the file
can remove that refusal. Verification then detects a changed log, and detects a truncated or
rebuilt one only against a head exported earlier. Times are UTC from the machine's clock.
Reviewer IDs are whatever your application supplies.

| Action | Recorded in | What protects it |
| --- | --- | --- |
| An item submitted, shown or decided, including which fields were edited | Event log | Chain. For real items, content only as salted hashes |
| A write requested, carried out, failed or blocked; a planted item withdrawn after a decision | Event log | Chain |
| Feedback given to a reviewer | Event log | Chain |
| A queue's AI system version first recorded or changed | Event log | Chain |
| A qualification session started with its pass mark, an item judged, shown or answered, and the result | Event log, with items and judgments in `planted.db` | Chain, and verification of the stored items and judgments |
| Names of what a screen showed, when your application gives them | `planted.db`, with a hash in the log | Verification |
| A planted error imported, with the author named in its file. **Who ran the import is not recorded** | `planted.db` | The database refuses changes to its content. Verification covers it only **once it has been used** |
| A planted error approved, with the approver's name and time | `planted.db` | Recorded once. Verification covers it only **once the planted error has been used** |
| For a planted error drafted from real output: who confirmed the original and who checked it for real data, as named when it was drafted | `planted.db` | The database refuses changes to it. Verification covers it only **once the planted error has been used** |
| A planted error retired, with who, when and why | `planted.db` | The database refuses a second change. **Verification does not cover it** |
| A planted error voided: who asked and confirmed, when, why, and your reference | `planted.db`, with a commitment in the event log | Chain and verification, once in effect. A request that is waiting or cancelled is recorded and not verified |
| A planted error due when none could be used | `planted.db` | **Not covered by verification** |
| Changes to settings, apart from the AI system version; reports made; alerts sent; verification runs; database upgrades; anyone reading or copying the files | **Not recorded** | Your controls |

Verification also does not cover the tables that summarise the log or the settings, such as
reviewers' current status. Record what the table leaves out in your own systems. In
particular, keep approval and retirement records in your quality system, as the
[planted-error procedure](sop-planted-errors.md) describes.

## 4. Open systems, § 11.30

An open system, where access is not controlled by those responsible for the records, needs the
closed-system controls as appropriate and more, such as encryption and digital signatures.
**ReviewProof encrypts neither its databases nor its reports, and makes no digital
signatures.** Its hash chain is not a digital signature: it identifies no one. It is designed
to run inside your controlled environment. If your use is an open system: **[FILL]**.

## 5. Electronic signatures

**ReviewProof has no electronic signatures. A recorded decision, planted-error approval or
qualification result is not one.** None carries a printed name or the meaning of a signature,
and no identity is verified. If a predicate rule or your procedures require a signature, apply
it in a system that provides it.

| Clause | In short | ReviewProof | How you meet it, if you use signatures |
| --- | --- | --- | --- |
| § 11.50 | A signed record shows the signer's printed name, date and time, and the meaning of the signature | Not provided | **[FILL]** |
| § 11.70 | Signatures are linked to their records so they cannot be copied or moved to falsify one | Not provided | **[FILL]** |
| § 11.100 | Each signature is unique to one person, identity is verified first, and the organisation certifies to FDA that its electronic signatures are legally binding | Not provided | **[FILL]** |
| § 11.200 | Signature components and their controls, such as two distinct identification components when not biometric | Not provided | **[FILL]** |
| § 11.300 | Controls for identification codes and passwords | Not provided: it has none | **[FILL]** |

## 6. Conclusion

| | |
| --- | --- |
| Records and signatures subject to Part 11 | **[FILL]** |
| Gaps, how each is closed, and any accepted with a reason | **[FILL]** |
| Assessed by, and date | **[FILL]** |
| Approved by, and date | **[FILL]** |

Data-integrity expectations such as ALCOA+, and rules outside the United States, such as EU
GMP Annex 11, are not mapped here.
