# Module 03 — Database Design

**Diagram for this module:** [`diagrams/03-er.svg`](diagrams/03-er.svg)

## The schema unique to this module

The "real" database in this system is whatever service owns the source-of-truth data (a `products` table, a `users` table) — this project's e-commerce and URL-shortener schemas already cover that ground. What's specific to cache invalidation is the **outbox / invalidation-event log** that turns a committed write into a published event — a direct application of [The Transactional Outbox & CDC](../content/hld-building-blocks/transactional-outbox-cdc.md), not a new pattern.

**`cache_invalidation_outbox`**

| Column | Type | Notes |
|---|---|---|
| `id` | bigint, PK | |
| `cache_key` | text | the logical key affected, e.g. `product:123` |
| `new_version` | bigint | monotonically increasing per `cache_key` |
| `created_at` | timestamp | written in the same transaction as the business write |
| `published_at` | timestamp, nullable | set by the relay once the event has been handed to Kafka; null = not yet published |

## Why this table, and why these columns

The row is written in the **same transaction** as the actual business write (the price update, the avatar change) — this is the whole point of the outbox pattern: the one atomicity guarantee that matters (the write and the fact-of-the-write agree) only ever has to span a single database, which databases already provide. `published_at` being nullable is what lets the relay find its own work: `SELECT * FROM cache_invalidation_outbox WHERE published_at IS NULL ORDER BY id` is the relay's entire query, and it's a normal indexed scan, not a full-table sweep.

## Indexes

- A single index on `published_at` (or a partial index `WHERE published_at IS NULL`) is the only one this table needs — the relay's one query pattern is "give me the unpublished rows, in order," and nothing else reads this table by any other key.
- `cache_key` is not separately indexed here; nothing queries this table BY key — it's a write-and-relay log, not something looked up interactively.

## Consistency

- The outbox row and the business write commit atomically together, by construction — there is no window where one exists without the other.
- `published_at` is set asynchronously, after the fact, by the relay — this table is intentionally eventually consistent with respect to "has this been sent yet," which is fine, since nothing downstream depends on that column except the relay's own bookkeeping.

## Scaling the schema

- This table is high-write-frequency but each row is tiny and short-lived — a background job deletes (or the relay itself deletes) rows once `published_at` is set and some retention window has passed, keeping the table small regardless of overall write volume.
- If a single service's write volume makes even this log contend, it shards the same way the rest of that service's tables already do — this table doesn't introduce a new sharding question, it inherits whichever one its owning service already answered.

## Connecting it back

The chain holds: module 01's requirement that a write must "eventually" reach every cache, without the write itself ever blocking on it, is exactly why an outbox row (not a synchronous call to Kafka) is what the transaction actually commits — and module 02's version-based race guard only works because every event carries the same `new_version` this table generates, in order, per key.
