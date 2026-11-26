# ADR-002: Event log and hash chain

- **Status:** Accepted
- **Date:** 2026-11-25
- **Amended:** 2026-11-26 (the commitment covers the target ID and the planted error itself;
  payload hashes are salted; per-field hashes, and numbers hashed by value;
  trace and review-context hashes; checking events before relying on them)

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
- Each item's payload hash is `SHA-256(canonical_json([payload_salt, payload]))`, where
  `payload_salt` is 32 random bytes of its own for that item, written as 64 hex characters and
  kept in `planted.db.item_registry`. Payloads **may** contain numbers with decimals (a dose,
  for example); NaN and infinity are rejected. Object keys must be strings.
- In payload and field hashes, a decimal with no fractional part is written as a whole number
  (`10.0` as `10`). JavaScript and others make no difference between them, so a review screen
  that sends an item back must not appear to have changed it.
- Every `submitted` event's data holds `commitment`, `payload_hash` and `field_hashes`: for
  each top-level field, `SHA-256(canonical_json([payload_salt, "field", name, [value]]))`,
  with an empty list when the field is absent. It covers every property in the item schema
  and every other field present. Real and planted items are treated alike. An edit's
  `decided` event holds `edited_fields`, which ReviewProof works out by comparing these, and
  `final_payload_hash`.
- Every `submitted` event also holds `trace_hash`, a salted hash of the item's stored trace
  records, or of an empty list. Every `shown` event holds `context_hash`, a salted hash of
  the host's account of the screen, or of its absence. All items have both, so neither
  shows which have a trace. `rp verify --planted` checks the restricted records against
  them. A trace keeps only each step's ID, type, time and salted input and output hashes.
- Each event carries a `format_version`, so the canonical form can change without breaking old
  chains.

### Chain

- `hash = SHA-256(prev_hash_hex + canonical_json(event_without_hash))`
- The first event's `prev_hash` is 64 zeros.
- `event_id` is a gapless integer sequence and is part of the hashed record.
- Appending reads the head and inserts inside one `BEGIN IMMEDIATE` transaction, so two writers
  cannot fork the chain.
- `rp verify` reports the first broken `event_id`, the event count and the head hash. It opens
  the databases read-only. It also requires the stored `data` text to be in canonical form.
- The chain alone cannot detect a fully rebuilt database or removal of the newest events. The head
  hash and event count exported to storage the tool cannot write to are what detect those.
  `rp verify --expect-head` checks that the event with the exported number still has the
  exported hash. `--export-head` writes only after verification succeeds, and never over an
  existing file.

### Checking before relying

Before an execution, an edit or feedback, each event of that item is checked: its hash, and
its links to the events on either side. This is cheap and catches an edited entry, but not a
log rewritten from that entry onwards. Only `rp verify` with an exported head catches that.

`blocked` events hold a `reason`: `planted`, `payload_changed`, `check_failed` or
`reserved_target`. `withdrawn` events hold the `outcome`, `catch` or `miss`.

### Event types

`context_registered`, `submitted`, `shown`, `opened`, `edited`, `decided`, `execute_requested`,
`executed`, `execute_failed`, `blocked`, `withdrawn`, `feedback_sent`.

Every item starts with a `submitted` event, so metrics need no mutable table.

### Planted commitment

Every `submitted` event, for real and planted items alike, carries:

`commitment = SHA-256(canonical_json([item_id, kind, planted_error_id, target_id, salt,
fingerprint]))`

where `salt` is 32 random bytes kept in `planted.db.item_registry`, written in the hashed list
as 64 hex characters. Covering the target ID means a redirected record is detected too.

`fingerprint` is null for a real item. For a planted item it is the SHA-256 of the canonical
JSON list of these stored `planted_errors` values, in this order: `planted_error_id,
queue_id, defect_class, defect_field, expected_decision, payload, correct_value, notes,
reserved_target_id, author, imported_at, approved_by, approved_at, second_approved_by,
second_approved_at`. So changing the item or how it is scored after use, for example to turn
a miss into a catch, is detected. Retirement is left out because it happens after use.
Approvals are included, so they are locked once a planted error has been used. This shows
that they have not changed since; it does not show that they were genuine.

- A reader of `oversight.db` cannot tell real from planted, because every commitment looks random.
- Anyone with both files can check every registry row against the chain. If a row was changed
  after submission, for example to relabel a planted error as real so that a miss disappears,
  `rp verify` reports it.
- The guard checks the commitment before executing; a mismatch fails closed.

Metrics read the event log, plus the registry **only after** its commitments have verified.

## Consequences

- `blocked` and `withdrawn` events show that an item was planted, but only after the decision,
  when the reviewer is due feedback anyway.
- Payload hashes are salted so that a planted error used on several items does not produce a
  repeated hash. The cost: matching an original document to its logged hash needs the item's
  salt, so it needs access to both databases.
- Event times come from the machine running the SDK; its clock must be controlled.
