# ADR-001: Storage in two SQLite files

- **Status:** Accepted
- **Date:** 2026-11-25
- **Amended:** 2026-11-26 (target IDs, token hashes, database protections)

## Context

The design calls for a local SQLite store, planted-error data restricted to QA
admins, and `is_planted` never exposed to the host. SQLite has no per-table permissions. A pharma
IT review also favours very few dependencies.

## Decision

**Two database files**, each protected by its own operating-system permissions:

| File | Tables |
| --- | --- |
| `oversight.db` | systems, queues, reviewers, items, events, qualifications, review_context, traces |
| `planted.db` | planted_errors, item_registry |

- `items` has **no** `is_planted` or `planted_error_id` column. Both live in a new table,
  `planted.db.item_registry`: `item_id, kind (real | planted), planted_error_id, target_id,
  salt, payload_salt`. This makes ten tables in total.
- **Target IDs are in the registry, not in `items`.** Planted items have target IDs from a
  reserved range, so a target ID in `oversight.db` would show which items are planted.
- `items` stores only a **hash of each token**, so reading the file does not yield tokens. It
  also records `system_id`, the context the item came from, because a queue moves on to later
  contexts.
- **Every** item gets a registry row, real or planted. Execution requires a row that positively
  says `real`. A missing row is an error, never "real".
- Writing an item takes two files, and SQLite does not guarantee an atomic commit across attached
  databases in WAL mode. The registry row is committed **first**, then the item and its
  `submitted` event. A crash between them leaves a harmless unused registry row. The reverse
  order could not make a planted item executable either, because a missing row blocks execution.
- `events` has triggers that abort UPDATE and DELETE. An insert trigger also requires each row
  to extend the chain by exactly one link. Without it, `INSERT OR REPLACE` could overwrite an
  event without firing the DELETE trigger.
- `items` and `item_registry` rows are permanent. In `planted_errors`, content cannot change
  after import, approvals and retirement are set once, rows cannot be deleted, and the author
  and both approvers must be different people (names compared ignoring case and spaces).
- `queues` also records the risk rating, `gxp`, feedback timing and an optional `site` for
  site-level reporting.
- Schema changes are forward-only migrations tracked in a `schema_version` table in each file.
- **Runtime dependency: PyYAML only.** The CLI uses `argparse`, reports use the standard library,
  alerts use `smtplib` and `urllib`. LangGraph is an optional extra.
- Item schemas are JSON Schema files. At runtime they are checked by a **small built-in validator**
  that supports `type`, `properties`, `required`, `additionalProperties`, `enum`, `pattern`,
  `items`, `minItems`, `maxItems`, `minLength`, `maxLength`, `minimum` and `maximum`. It
  **rejects a schema that uses any other validation keyword**, so nothing is silently skipped.
  `jsonschema` is a development dependency, used to cross-check the validator in tests.

## Consequences

- The process running the SDK needs read access to `planted.db`. The file permissions keep out
  *people* who should not see it, not the host application's own service account. Reviewers must
  have no access to either file.
- `ov.planted` management functions need write access to `planted.db`, held by QA admins.
- Backups and restores must treat the two files as a pair.
- SQLite allows one writer at a time: enough for a pilot. PostgreSQL is the v2 target.
- Hosts with complex item schemas will hit unsupported keywords. A host-supplied validator
  function is the fallback if that happens in the pilot.
