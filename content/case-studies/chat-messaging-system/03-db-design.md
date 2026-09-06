# Module 03 — Database Design & Scaling

![Schema: conversations, messages, participants, delivery_receipts](diagrams/er.svg)

## From entities to schema

**Entities:** `conversations` (id, type: direct/group, created_at, last_message_at), `participants` (conversation_id, user_id — the membership join table), `messages` (id, conversation_id, sender_id, body, client_msg_id, created_at), `delivery_receipts` (message_id, user_id, status, status_rank, updated_at).

## Why the message ID is scoped to the conversation, not global

**The message ID is a monotonic ID scoped to the conversation, not a global auto-increment.** History pagination and ordering are always asked *within one conversation* ("give me this conversation's next page"), never across all conversations globally — a per-conversation monotonic ID (e.g. a Snowflake-style ID with the conversation embedded, or a per-conversation counter) keeps that the cheap, naturally-ordered query it needs to be. It also sets up the sharding key: sharding `messages` by `conversation_id` (cross-ref [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md)) keeps one conversation's entire message history on one shard, so pagination never fans out across shards — exactly the same reasoning this guide's sharding page gives for choosing a key that matches the query that actually runs constantly.

## Why `delivery_receipts` is a separate table, not columns on `messages`

A message has *one* row but potentially many recipients (any group chat), each needing their own independent delivery/read state; a separate table keyed by `(message_id, user_id)` is the only shape that lets one recipient's read receipt update without touching every other recipient's row. Collapsing this onto `messages` would mean a message sent to 50 group members needs 50 different "columns" of state on a single row — the join table is the schema saying "this state is per-recipient," which the requirements already demand.

## Indexes

- `messages(conversation_id, client_msg_id)` — **unique**, and the actual concurrency-safety mechanism behind the idempotency check in Module 02's pseudocode, not just an optimization; the second concurrent insert for the same key fails this constraint at the database level.
- `messages(conversation_id, id)` — composite, serves paginated history reads directly (`WHERE conversation_id = ? ORDER BY id`), the single most common read in the entire system.
- `participants(user_id)` — serves "which conversations is this user in," needed for the conversation-list query and for validating a sender actually belongs to a conversation before accepting their message.
- `delivery_receipts(message_id, user_id)` — the primary key itself, since every receipt lookup and update is scoped to exactly one `(message, recipient)` pair.

## Consistency

- **`messages`:** must be strongly consistent at write time — the append that happens before the online/offline branch (Module 01) has to be durable and immediately visible to a page-history read, since a client that reconnects moments later has to see every message it missed, with none silently absent.
- **`delivery_receipts`:** strongly consistent per-row (the monotonic `status_rank` guard from Module 01's Concurrent-User table depends on reads seeing the latest write), but eventually consistent is fine *across* different recipients' rows for the same message — there's no requirement that all recipients' receipts update in lockstep.
- **`conversations.last_message_at`** (denormalized, below): eventually consistent is explicitly acceptable — a conversation list that's a few hundred milliseconds stale in its sort order is invisible to a user scrolling their inbox.

## Denormalization

`conversations.last_message_at` is kept as a column, updated on every send, so the conversation-list query (`GET /conversations`, sorted by recent activity) never has to scan `messages` to find each conversation's latest timestamp — the same "freeze a number that's expensive to recompute live" instinct as this guide's [e-commerce schema](../../database-design/ecommerce-schema-worked-example.md)'s `total_amount`.

## Scaling the schema

- **Sharding `messages`** by `conversation_id`, as established above — one conversation's full history stays on one shard, so pagination is always a single-shard query.
- **`delivery_receipts` scales with recipient count, not message count** — a group chat's single message produces one row per recipient, making this table's growth rate a function of group size, worth sizing separately from the message-volume math in Capacity Estimation.
- **Read replicas vs. sharding, again:** conversation-list reads and history pagination are both read-heavy and benefit from read replicas; `messages`' write volume at 700,000/sec peak is what actually demands sharding. Reaching for one when the other is the real bottleneck is the mistake to avoid, the same distinction this guide draws in every case study's DB design.

## Connecting it back

Look at all three modules together: Module 00's split requirement — the message body needs at-least-once, never-lose guarantees, while receipts are allowed to be best-effort — is why the durable write in Module 01 happens before the online/offline branch is even evaluated, why `DeliveryStatus` is an enforced state machine rather than a trusted client-reported boolean, and why `delivery_receipts` is a separate, independently-consistent table rather than a column bolted onto `messages`. Nothing in this schema is arbitrary — every table boundary and every index traces back to which half of Module 00's tension it exists to serve.
