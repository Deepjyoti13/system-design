# Module 03 — Database Design & Scaling

![Schema: payment_intents, double-entry ledger_entries, and the webhooks_outbox](diagrams/er.svg)

## From entities to schema

Three entities fall out of the requirements directly:

- **`payment_intents`** `(id, idempotency_key UNIQUE, merchant_id, amount, currency, status, processor_ref, created_at, updated_at)` — the state machine's home, and the single source of truth for "did this charge happen."
- **`ledger_entries`** `(id, payment_id, account_id, entry_type: debit|credit, amount, created_at)` — one payment produces exactly two rows; append-only, never updated.
- **`webhooks_outbox`** `(id, payment_id, event_type, payload, created_at, sent_at)` — the relay's work queue.

## Why `idempotency_key` gets a unique index, non-negotiably

**The unique constraint on `idempotency_key` is the actual concurrency-safety mechanism**, not a nice-to-have layered on top of application logic — it's what makes the "duplicate request" race from Module 01's Concurrent-User Handling a guaranteed, database-enforced outcome rather than an application-level check that could itself race. Two concurrent identical requests both reading "no existing intent" and both attempting an insert is a real possible interleaving in application code; it is not a possible outcome at the database level once the index exists, because the second insert simply fails the constraint.

## Why `status` is a constrained enum with enforced transitions, not a free string

A free-text `status` column lets any code path write any value, including "succeeded" written twice or "refunded" written before "succeeded" ever landed. Modeling the state machine's legal transitions as conditional updates (`UPDATE ... SET status = 'succeeded' WHERE status = 'pending'`) means an out-of-order or duplicate write is a silent no-op — zero rows affected — rather than a corrupted history. This is the database-level enforcement of the same state-machine discipline Module 02's LLD names explicitly.

## Why strongly consistent and relational, not eventually consistent

Money is the canonical case both [ACID vs BASE](../../database-design/acid-vs-base.md) and [SQL vs NoSQL](../../database-design/sql-vs-nosql.md) already name for exactly this choice — a balance that's briefly wrong is not an acceptable trade the way a briefly-stale like-count is. The relational model also buys the transactional guarantee the whole design leans on: `payment_intents` status update and `webhooks_outbox` insert committing atomically, in the same transaction, is what makes the outbox pattern actually safe — a document or key-value store without multi-row ACID transactions would need a different mechanism entirely to get that same guarantee.

## Indexes

- `payment_intents(idempotency_key)` — **unique**, the concurrency-safety mechanism above; every write path checks it.
- `payment_intents(merchant_id, created_at)` — a merchant's transaction history and dashboard queries filter by merchant first, then a date range; this composite index serves that access pattern directly instead of scanning.
- `payment_intents(status, updated_at)` — the reconciliation worker's query is exactly "rows stuck in `processing` for longer than X" — this index turns that into an index range scan instead of a full table scan as the table grows past billions of rows.
- `ledger_entries(account_id, created_at)` — "this account's transaction history, in order" is the ledger's primary read pattern; no secondary index is needed on `payment_id` alone since a payment's two entries are always looked up together, by account.
- `webhooks_outbox(sent_at)` where `sent_at IS NULL` (a partial index) — the relay's poll query is "unsent rows," and a partial index over just the unsent subset stays small and fast even as the historical outbox table grows into the billions of rows.

## Consistency

- **`payment_intents`:** must be strongly consistent — a read immediately after a write (the client's own `GET /payments/{id}` right after `POST /payments`) has to reflect the true current status, never a stale `pending` for a payment that already succeeded. This is a hard requirement, not a tunable trade-off, given the "never silently lose or duplicate a payment" bar from Module 00.
- **`ledger_entries`:** strongly consistent at write time (the pair is written in the same transaction as the status update), but read-heavy reporting queries (a merchant's full statement, aggregated over a month) are a legitimate candidate for a read replica — the replica's small replication lag doesn't change what actually happened, only how promptly a report reflects the very latest transaction.
- **`webhooks_outbox`:** eventually consistent by design — a merchant's webhook can lag the actual status change by however long the relay takes to notice and deliver it, which is an explicit, acceptable trade named in Module 01 (the merchant's own `GET /payments/{id}` always shows the true current status in the meantime).

## Scaling the schema

- **Sharding, once volume demands it:** by `merchant_id` (or the payer's `account_id` for the ledger specifically), keeping one account's full ledger on one shard so "what does this account's balance show right now" is a single-shard lookup rather than a fan-out-and-merge query across shards. An arbitrary hash-based shard key would scatter one account's history everywhere, turning the single most common query into the most expensive one — the shard key has to match the access pattern that actually runs constantly, not a desire for perfectly even distribution.
- **The ledger outgrows the intents table roughly 2:1** (Module 00's capacity math), which is itself a reason to let `ledger_entries` scale and shard independently from `payment_intents` rather than assuming one sharding scheme fits both.
- **Read replicas vs. sharding, again:** replicas solve read throughput for reporting and dashboards; sharding solves write volume and total data size on the ledger. Reaching for one when the other is the actual bottleneck is the mistake to avoid, the same distinction this guide draws in every other case study's DB design.

## Connecting it back

Look at all three modules together now: Module 00's "never double-charge, never silently lose a payment" requirement is why Module 01 puts the local-transaction intent write before the processor call at all; that same requirement is why `idempotency_key` shows up as a unique index here rather than an application-level check; and the double-entry ledger's append-only, never-mutated design is what makes "sum every entry, get zero" a mechanical audit instead of a hope. Nothing in this schema is arbitrary — every constraint and index traces back to the one requirement that dominates this entire case study.
