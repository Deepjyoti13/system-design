# Module 03 — Database Design

**Diagram for this module:** [Ephemeral Stories — ER diagram](https://claude.ai/code/artifact/e4dc7d0a-a9f6-46be-bcd1-6de15b7c503c)

## From entities to schema

Three entities, matching Module 01's requirements directly:

- **`users`** — not detailed here (referenced by id only).
- **`stories`** — the post itself: media reference, status, and (fallback-only) expiry timestamp.
- **`story_views`** — one row per (story, viewer) pair, for the viewer list and analytics.

Open the [ER diagram](https://claude.ai/code/artifact/e4dc7d0a-a9f6-46be-bcd1-6de15b7c503c) for the full field list. A few decisions are worth walking through rather than just reading off the diagram.

### Why `stories` is partitioned by `created_date`, non-negotiably

This is the single most important decision in this module, and it's a direct consequence of Module 01's expiry mechanism. Partitioning by day turns bulk cleanup into `ALTER TABLE ... DROP PARTITION` — an **O(1)** metadata operation — instead of `DELETE WHERE expires_at < NOW()` against a table doing on the order of a billion inserts a day, which would be enormous write amplification (and slow enough to risk becoming the correctness bug Module 01 is built to avoid). The partition drop is cleanup, not correctness — correctness is Redis's job (Module 01) — which is exactly why the drop is allowed to lag by hours with zero user impact.

### Why `expires_at` exists on the row at all, if Redis is the real gate

Because Module 02's LLD explicitly designed a fallback: if Redis is down, `getTray` falls back to checking this column directly. Without it, a Redis outage would mean either serving expired content (unacceptable) or serving nothing (also bad, and a bigger blast radius than necessary). It's a deliberately redundant field, kept in sync at write time, for exactly one purpose: surviving a cache outage without a correctness compromise.

### Why `story_views` has a unique constraint on `(story_id, viewer_id)`, not just an index

This is what makes `insertIfAbsent` (Module 02) actually idempotent at the database level, not just in application code — a second insert attempt for the same viewer fails/no-ops instead of creating a duplicate row, which is what keeps the live view count (a `SCARD` over a Redis Set built from these same events) from ever double-counting a view.

### Why `story_views` also gets a composite index on `(story_id, viewed_at)`

The poster-facing query is always "who viewed *this* story, in order" — never "all views across all stories." A composite index with `story_id` first lets MySQL narrow to one story's rows before it even considers ordering by time, the same reasoning the root project's URL shortener uses for `click_events`.

## SQL vs. NoSQL, for this system specifically

This design uses MySQL for both tables, and it's worth being explicit about why rather than defaulting to it:

- **Partition management** (drop-a-day-of-data) is a first-class relational feature. A pure key-value store would need its own equivalent (DynamoDB's native TTL is the closest analog) — which would mean running *two* independent expiry mechanisms (Redis's gate plus the store's own TTL sweep) instead of one clean one.
- The viewer-list query ("who viewed this story, in this order") is a straightforward range-indexed query, awkward to express well in a pure KV model.
- If this system only ever needed "is this story visible" (no viewer list, no per-user dashboard), a pure key-value store alongside Redis would be a legitimate, simpler choice — the requirements (a viewer list, and bulk-expiry-by-day) are what justify SQL here, not a default preference for it.

## Sharding and replication

- **Sharding key: `user_id` hash**, applied to both `stories` and `story_views`. Almost every query in this system is scoped to "this user's stories" or "this story's views" — sharding by `user_id` co-locates a user's own stories and (via the FK relationship) keeps related data reachable without cross-shard fan-out on the hot paths. Partitioning by day then happens *within* each shard — two independent dimensions, not one.
- **Replication:** primary + read replica(s) per shard. Writes (create, recordView) go to the primary; the Redis-down fallback and the following-list read in `getTray` go to a replica.
- **Consistency:** eventual on replica reads — a few hundred milliseconds of replication lag on "who do I follow" is invisible to a user. The one place strong consistency is non-negotiable is the Redis visibility gate itself (Module 01), which is precisely why that check was pulled out of the SQL replication path entirely rather than relying on read-your-writes consistency from a lagging replica.

## Connecting it back

Trace the chain once more, end to end: the 24-hour non-functional requirement (Module 01) is why there's a Redis gate at all; the gate being the *real* source of truth (not the SQL row) is why `expires_at` in this module is explicitly labeled "fallback only" rather than authoritative; and the daily partitioning here is what makes the whole approach operationally cheap instead of just architecturally clean on paper. Requirement → architecture decision → interface → schema — the same chain the root project's URL shortener walks, reproduced here for a system where the interesting constraint is disappearance instead of permanence.
