# Module 03 — Database Design & Scaling

![Schema: conversations, messages, participants, delivery_receipts](diagrams/er.svg)

**Entities:** `conversations` (id, type: direct/group, created_at), `participants` (conversation_id, user_id — the membership join table), `messages` (id, conversation_id, sender_id, body, client_msg_id, created_at), `delivery_receipts` (message_id, user_id, status, status_rank, updated_at).

**The message ID is a monotonic ID scoped to the conversation, not a global auto-increment.** History pagination and ordering are always asked *within one conversation* ("give me this conversation's next page"), never across all conversations globally — a per-conversation monotonic ID (e.g. a Snowflake-style ID with the conversation embedded, or a per-conversation counter) keeps that the cheap, naturally-ordered query it needs to be. It also sets up the sharding key: sharding `messages` by `conversation_id` (cross-ref [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md)) keeps one conversation's entire message history on one shard, so pagination never fans out across shards — exactly the same reasoning this guide's sharding page gives for choosing a key that matches the query that actually runs constantly.

**Indexes:** a unique index on `messages(conversation_id, client_msg_id)` (the idempotency check in the pseudocode above depends on this being fast and unique-enforced, not just fast); a composite index on `messages(conversation_id, id)` for paginated history reads; an index on `participants(user_id)` for "which conversations is this user in."

**Denormalization:** `conversations.last_message_at` is kept as a column, updated on every send, so the conversation-list query (`GET /conversations`, sorted by recent activity) never has to scan `messages` to find each conversation's latest timestamp — the same "freeze a number that's expensive to recompute live" instinct as this guide's [e-commerce schema](../../database-design/ecommerce-schema-worked-example.md)'s `total_amount`.
