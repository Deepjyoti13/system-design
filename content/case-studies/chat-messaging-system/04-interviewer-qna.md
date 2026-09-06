# Module 04 — Interviewer Q&A

**What happens when two of a user's devices send `message.send` for the same logical message at the same instant (e.g. a retry after a flaky connection)?**
The unique index on `(conversation_id, client_msg_id)` makes the second insert a no-op that returns the first attempt's `message_id` instead of creating a duplicate — this is exactly why `client_msg_id` is client-generated and sent with the original request, not assigned by the server after the fact.

**What happens when traffic spikes 10x for an hour (a major news event driving group-chat activity)?**
Message Service and the Connection Gateway tier are both stateless-per-request (aside from the gateway holding sockets) and scale horizontally to absorb it; the Presence Registry, being a single shared store, is the more likely bottleneck — it's sized with headroom for exactly this and sharded by `user_id` hash so no single node holds the whole registry. If it still saturates, presence lookups degrade to "assume offline, queue it" rather than blocking sends, trading a temporarily-higher offline-queue depth for keeping the send path itself unaffected.

**Why not just use the recipient's `last_seen` timestamp instead of a live Presence Registry?**
`last_seen` answers "when did we last hear from them," which is stale by definition; the routing decision needs "which specific gateway instance, right now, holds their socket" — a fundamentally different, live question that only a registry updated on connect/disconnect can answer.

**How would you support a group chat with thousands of members, where the current design's "push to every online recipient" fan-out gets expensive?**
This is the same push-vs-pull tension this guide's [News Feed](../news-feed-system.md) case study covers for feeds: fan-out-on-write (push to every member's queue at send time) is fine for a few hundred members but doesn't hold at thousands; past that threshold, switch that specific conversation to fan-out-on-read (recipients pull recent messages on reconnect/open instead of each one being individually pushed).

**Could `delivery_receipts` just be columns on `messages` instead of a separate table?**
Not cleanly — a message has *one* row but potentially many recipients (any group chat), each needing their own independent delivery/read state; a separate table keyed by `(message_id, user_id)` is the only shape that lets one recipient's read receipt update without touching every other recipient's.

**Would you make message delivery exactly-once?**
No — at-least-once delivery plus the `client_msg_id` idempotency check (same mechanism as the retry case above) is cheaper to build and just as correct in practice; true exactly-once delivery across a network is the kind of guarantee [Distributed Transactions](../../hld-building-blocks/distributed-transactions-saga.md) shows is extremely expensive to get exactly right, for a problem idempotency already solves at the consumer.

**What's the actual failure mode if the offline-delivery queue itself goes down?**
Messages to offline recipients queue up in the durable `messages` table regardless (the write there happens before the online/offline branch, not after) — the queue is only the *mechanism* that wakes a reconnecting client up quickly; if it's down, a reconnecting client still gets its backlog by querying unread messages directly, just without the low-latency push the moment it reconnects.

**Would you shard the Presence Registry the same way as the message store?**
No, and that's deliberate — Presence Registry is sharded by `user_id` hash (an even, arbitrary spread, since presence lookups are always single-user), while `messages` is sharded by `conversation_id` (so one conversation stays together); the two datasets have different access patterns and there's no reason to force them onto the same shard key.
