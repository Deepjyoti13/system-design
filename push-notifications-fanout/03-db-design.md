# Module 03 — Database Design

**Diagram for this module:** [Push Notifications — schema / ER diagram](https://claude.ai/code/artifact/70a7410e-935d-4bb4-920f-3c094613d74b)

## From entities to schema

Three entities fall out of the requirements in module 01:

- **`device_tokens`** — the mapping the entire dispatch path exists to read: `user_id → valid device tokens`.
- **`notification_events`** — one row per event the system was asked to fan out (a post, a 2FA code, a campaign).
- **`delivery_log`** — one row per actual send attempt, for audit and analytics — *not* the hot dedup path (that's Redis, module 01).

Open the [ER diagram](https://claude.ai/code/artifact/70a7410e-935d-4bb4-920f-3c094613d74b) for the full field list. A few decisions worth walking through rather than just reading off the diagram:

### Why `device_tokens` is NoSQL, not relational

The only query this table ever serves in the hot path is "give me every valid token for `user_id X`" — a single-key lookup, never a join. At the assumed 2B rows, a relational primary would need sharding to hold this comfortably anyway — so this design skips straight to a key-value/wide-column store (DynamoDB-shaped: partition key `user_id`, one item per token) and gets horizontal scale as a property of the store instead of something the application has to build. If this table also needed to answer "which users are on app version X" or other ad-hoc analytical queries, that would tip the balance back toward relational — it doesn't, so it isn't.

### Why `(device_id, platform)` gets a unique constraint, non-negotiably

This is what makes the registration `UPSERT` from module 01's Race 3 actually work: `INSERT ... ON CONFLICT (device_id, platform) DO UPDATE` only prevents a duplicate-token window if the constraint exists to conflict *on*. Without it, a token rotation race could leave two rows for the same physical device, and the target resolver would then send two notifications to what the user experiences as one phone.

### Why `delivery_log` is not in the same store as `device_tokens`

`delivery_log` takes a row on every single send attempt — at the assumed peak, 833,000 writes/sec. Put that in the same operational store as `device_tokens` and you've made the *read-hot* path (target resolution, which every single fan-out depends on) compete for capacity with the *write-hot* path (delivery logging, which nothing else depends on synchronously). This design routes `delivery_log` writes into a separate append-only pipeline — dispatch workers publish delivery results to a Kafka topic, which is consumed into a columnar/time-series store (ClickHouse or equivalent) built for exactly this shape: high write volume, range queries by time, no updates. This mirrors the same reasoning the URL shortener project uses for `click_events`: the highest-write-volume table is the first one to split off the primary operational store.

### Why `notification_events` stays relational

Unlike the other two, this table is genuinely low-volume relative to the per-device fan-out it triggers (one row per *event*, not per *device*), and it's queried by producers and ops dashboards in ways that benefit from real indexes and range scans (`type`, `created_at`). There's no scale pressure here that would justify giving up SQL's ergonomics.

## Indexes

- `device_tokens`: partitioned/indexed by `user_id` (the only hot read); unique constraint on `(device_id, platform)`.
- `notification_events`: composite index on `(type, created_at)` — mirrors the URL shortener's `click_events` reasoning: queries are almost never "all events ever," they're "transactional events in the last hour," so a composite index that narrows by type before considering time is what keeps that fast.
- `delivery_log` (in its time-series store): partitioned by time, with `event_id` as a secondary dimension for "show me every delivery attempt for this one event" lookups.

## Sharding, replication, consistency

- **`device_tokens`**: partitioned by hash of `user_id` — every read is already scoped to one `user_id`, so no cross-shard fan-out is ever needed for the hot path. `is_valid` is an **eventually consistent** flag (module 01's Race 2 explicitly accepts a small window where a stale token can still receive one wasted send attempt) — there is nothing here strong consistency would actually protect.
- **`notification_events`**: single primary + read replica is enough at this table's volume; each row is written once and never mutated, so there's no write-contention story to design for.
- **`delivery_log`**: partitioned by time (natural for a time-series/columnar store); consistency is "eventually queryable" — a delivery result might take seconds to show up in analytics, which is fine because nothing on the send path reads it back synchronously.

## Connecting it back

The Redis dedup cache in module 01 exists because `delivery_log` is explicitly *not* fast enough (or the right shape) to serve as a synchronous "have I sent this?" check at 833K/sec — that's the same "cache in front of a store that isn't built for the hot path's access pattern" reasoning as the URL shortener's cache-in-front-of-the-primary-DB. And the `(device_id, platform)` unique constraint here is what makes the `TargetResolver` interface in module 02 trustworthy — it can assume "one row per device" without having to defend against duplicates itself. Requirement → architecture decision → schema, same chain, different system.
