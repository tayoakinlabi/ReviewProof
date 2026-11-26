# ADR-002: Event log and hash chain

- **Status:** Accepted
- **Date:** 2026-11-25
- **Amended:** 2026-11-26 (the commitment covers the target ID)

## Context

The event log is the evidence. It must be tamper-evident, hashes must reproduce on any supported
Python version and OS, and all metrics must be rebuildable from it. The planted/real registry is
outside the chain (ADR-001), so changing it must be detectable too. The log must not tell a reader
which pending items are planted.

## Decision

### Canonical form

`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`,
encoded as UTF-8.

- **Event records contain no floats.** Durations are integer milliseconds; rates are never stored.
- Timestamps are UTC strings in one fixed format: `YYYY-MM-DDTHH:MM:SS.ffffffZ`.
- Payload hashes use the same function over the payload. Payloads **may** contain numbers with
  decimals (a dose, for example); NaN and infinity are rejected. Object keys must be strings.
- Each event carries a `format_version`, so the canonical form can change without breaking old
  chains.

### Chain

- `hash = SHA-256(prev_hash_hex + canonical_json(event_without_hash))`
- The first event's `prev_hash` is 64 zeros.
- `event_id` is a gapless integer sequence and is part of the hashed record.
- Appending reads the head and inserts inside one `BEGIN IMMEDIATE` transaction, so two writers
  cannot fork the chain.
- `rp verify` reports the first broken `event_id`, the event count and the head hash.
- The chain alone cannot detect a fully rebuilt database or removal of the newest events. The head
  hash and event count exported to storage the tool cannot write to are what detect those.
  `rp verify --expect-head` checks that an exported hash is still in the chain.

### Event types

`context_registered`, `submitted`, `shown`, `opened`, `edited`, `decided`, `execute_requested`,
`executed`, `execute_failed`, `blocked`, `withdrawn`, `feedback_sent`.

Every item starts with a `submitted` event, so metrics need no mutable table.

### Planted commitment

Every `submitted` event, for real and planted items alike, carries:

`commitment = SHA-256(canonical_json([item_id, kind, planted_error_id, target_id, salt]))`

where `salt` is 32 random bytes kept in `planted.db.item_registry`, written in the hashed list
as 64 hex characters. Covering the target ID means a redirected record is detected too.

- A reader of `oversight.db` cannot tell real from planted, because every commitment looks random.
- Anyone with both files can check every registry row against the chain. If a row was changed
  after submission, for example to relabel a planted error as real so that a miss disappears,
  `rp verify` reports it.
- The guard checks the commitment before executing; a mismatch fails closed.

Metrics read the event log, plus the registry **only after** its commitments have verified.

## Consequences

- `blocked` and `withdrawn` events show that an item was planted, but only after the decision,
  when the reviewer is due feedback anyway.
- A planted error reused on several items produces the same payload hash each time, which would
  reveal it to anyone who can read `oversight.db`. Reviewers must not have that access. Whether to
  salt payload hashes per item is to be decided when the log writer is built; it would trade this leak against
  being able to match an original document to its hash without the salt.
- Event times come from the machine running the SDK; its clock must be controlled.
