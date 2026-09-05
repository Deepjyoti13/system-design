# Module 03 — Database Design

**Diagram for this module:** [Schema / ER diagram](https://claude.ai/code/artifact/aa61dc0a-5ff6-41ce-aa85-1121711473ba)

## From entities to schema

Exactly **one** durable entity falls out of the requirements in module 01:

- **`conversation_receipts`** — one row per (conversation, user): how far that user has read, and their last known status.

Two entities are referenced but **not owned** here: `conversations` and `users` belong to the underlying messaging system. Open the [ER diagram](https://claude.ai/code/artifact/aa61dc0a-5ff6-41ce-aa85-1121711473ba) for the full field list.

### Why presence has no table at all

This is the decision worth walking through rather than reading off the diagram: **presence is not persisted anywhere durable.** It lives entirely as a Redis key (`user:{id}:online`, 30s TTL — see module 01). No table, no migration, no backup job. The reasoning: nothing in the functional requirements asks "who was online three days ago" — the product only ever asks "is this person online *right now*." Building a durable table for a fact nobody queries historically would be exactly the kind of unrequested durability the trade-offs table in module 01 already rejected for the fan-out mechanism. If a future requirement ever needs a presence *history* (e.g. "average daily active hours"), that's a new, explicitly-justified table — not a retrofit onto this one.

### Why `(conversation_id, user_id)` is the composite primary key, non-negotiably

The only two query patterns this table ever serves are "my read state for this conversation" (point lookup by both columns) and "everyone's read state for this conversation" (range scan with `conversation_id` as a prefix). A composite primary key `(conversation_id, user_id)` satisfies both without a second index — `conversation_id` first means the second query pattern doesn't need to scan unrelated conversations.

### Why `status` is an ordinal enum, not three booleans

Modeling delivery state as `is_sent`, `is_delivered`, `is_read` booleans would allow nonsensical states (`is_read=true, is_delivered=false`) and require updating multiple columns atomically to preserve the invariant that read implies delivered implies sent. A single ordinal column (`SENT=0 < DELIVERED=1 < READ=2`) makes "never regress" a one-line `GREATEST()` comparison instead of a set of cross-column invariants the application has to maintain by hand — this is the same LLD decision from module 02, showing up here as the schema that makes it enforceable.

## SQL vs. NoSQL vs. cache-only — for this table specifically

This design uses a relational, sharded store, and it's worth justifying rather than defaulting to it:

- The core write is a **conditional compare-and-set** (`UPDATE ... WHERE last_read_message_id < ?`) — this is exactly the shape a managed key-value store with conditional-write support (e.g. DynamoDB's conditional expressions) would also serve well. That's a legitimate alternative, not a wrong one.
- What tips this toward relational here specifically: the surrounding messaging system this feature hooks into **already operates a sharded relational cluster** for conversations and messages. Adding one small, simple table to an operational surface that already exists is cheaper than standing up and operating a second datastore for a single table. This is the same lesson the URL shortener's module 03 draws from its own SQL-vs-NoSQL section: the requirements and the surrounding system — not a default preference — justify the choice.
- If this feature existed in isolation, with no pre-existing relational cluster to piggyback on, a managed KV store with conditional writes would be the leaner call — fewer moving parts to operate for one table with one query shape.

## Scaling the schema

- **Sharding**: by `conversation_id` — the same shard key the underlying message store already uses. Every read is already scoped to one conversation, so this guarantees no cross-shard fan-out for either query pattern.
- **Row growth**: ~500M users × ~20 active conversations × ~40 bytes/row ≈ 400GB total — spread across 50–100 shards, each shard stays small enough to keep entirely in a modest amount of memory/cache, keeping the conditional UPDATE fast even under contention.
- **Replication**: single-region, single-leader-per-shard is sufficient at the stated scale (read-after-write consistency only needs to hold within a shard, which a single-leader design gives for free). Cross-region read replicas would only be worth adding once users are meaningfully geo-distributed — module 01 already flags this as a "what you'd revisit" item.

## Consistency & isolation

- Writes are single-row conditional UPDATEs — no multi-row transaction is ever needed, so the isolation level question mostly disappears: a single `UPDATE` statement is atomic by default in any relational engine.
- Cross-shard consistency is a non-issue because no query ever spans shards (see sharding above).
- The two-path design from module 01 (fast pub/sub push + durable async write) means this table's data is allowed to visibly lag the client's screen by up to a few hundred milliseconds — an explicit, bounded, self-healing inconsistency window, not an accident.

## Connecting it back

Trace the chain once more, the same way the URL shortener's module 03 does: the *requirement* that read state must never regress (module 01) drove the *architecture decision* to separate a fast pub/sub path from a durable path; that drove the *interface* decision in module 02 to push the check-and-write into one atomic database statement rather than application code; and that's what makes the single-column ordinal `status` design in this module actually sufficient — no application-level locking required anywhere in the whole system. Reproducing that chain on a new problem is the actual skill `04` will ask you to demonstrate.
