# Module 01 — Architecture & High-Level Design

![Chat system architecture: connection gateways, message service, presence, and the offline-delivery queue](diagrams/hld.svg)

## Monolith vs. microservices

The Connection Gateway and Message Service tier is pulled out on its own, not folded into a general Social Platform or User Service monolith, for a reason that's structural rather than just about load volume: the gateway tier is fundamentally **stateful** — each instance holds live WebSocket connections tied to specific users, which means it can't be scaled, deployed, or load-balanced the way a stateless request-response tier can. A stateless instance dying loses nothing; a gateway instance dying drops every connection it was holding, which is exactly why a client reconnect protocol has to exist at all (see Scaling & Reliability below). Bundling that connection-holding responsibility into a general-purpose monolith would force every unrelated code path in that monolith — profile settings, block lists, account preferences — to inherit the gateway's much stickier deployment and scaling story, for functionality that has nothing to do with holding a socket open.

If your product is small enough that live connections number in the thousands rather than hundreds of millions, a single process can hold all of them comfortably and this split isn't buying you anything yet — say so rather than defaulting to a separate gateway tier because it looks more scalable.

## Building blocks

- **Connection Gateway tier** — stateful, holds the persistent WebSocket per online client. This is a genuinely different scaling shape from the rest of this design, for exactly the reason [Long Polling, WebSockets & SSE](../../scalability-resilience/long-polling-websockets-sse.md) names: a stateless server can answer any client's next request, but a gateway instance has to be *that specific client's* instance for the life of the connection.
- **Presence Registry** — a fast shared store (Redis) mapping `user_id → gateway_instance_id` for every currently-connected user. This is the answer to the hard routing problem: when Message Service needs to deliver to a specific recipient, it looks up which gateway instance (if any) holds that recipient's socket, the same shape as [service discovery](../../scalability-resilience/service-discovery.md)'s registry, specialized to one user per entry instead of one service instance per entry.
- **Message Service** — receives `message.send`, writes it durably, then either pushes it directly (recipient online: found in the Presence Registry) or drops it onto the **offline-delivery queue** (recipient not found there).
- **Offline-delivery queue** — cross-ref [Message Queues & Pub/Sub](../../hld-building-blocks/message-queues-pubsub.md): a per-recipient backlog, drained the moment that recipient's gateway registers them back into the Presence Registry on reconnect.
- **Message Store** — the durable, sharded log of every message ever sent (schema below).

## Per-path walkthrough

**Online delivery path** — `Sender's Gateway → Message Service (durable write, in PARALLEL, not blocking) → PresenceRouter.locate(recipient) → Recipient's Gateway (push) → Recipient`. The durable write and the push happen concurrently, not sequentially — the recipient sees the message with sub-200ms latency, and the write completing is only guaranteed before the *sender's own* ack, never a precondition for the recipient's push.

**Offline delivery path** — `Sender's Gateway → Message Service (durable write) → PresenceRouter.locate(recipient) returns null → OfflineQueue.enqueue → (recipient reconnects, later) → PresenceRouter.register → OfflineQueue drains → Recipient's new Gateway (push)`. Nothing here is lost, only delayed — the durable write already happened before the online/offline branch was even evaluated, matching Module 00's requirement that a message can be delayed but never silently dropped.

**Presence-update path** — `Client connects → Gateway registers (user_id, device_id) → Presence Registry → fan-out presence.update to online contacts`. This path is best-effort and lossy by design (Module 00's "read/delivered receipts can lag by a second or two without anyone noticing"), unlike the two message-delivery paths above, which never are.

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Real-time transport | Persistent WebSocket per client | Long polling / short polling | A persistent connection is what makes sub-200ms push latency possible at all — cross-ref [Long Polling, WebSockets & SSE](../../scalability-resilience/long-polling-websockets-sse.md); polling means either constantly-wasted requests or delivery latency bounded by the poll interval |
| Presence granularity | Per-connection (`user_id, device_id`) | Per-user (single online/offline flag) | A user with a phone and a laptop open simultaneously needs "online" to mean *any* device connected, without one device's disconnect flipping the whole user offline |
| Message delivery guarantee | At-least-once + client-generated idempotency key | Exactly-once | True exactly-once delivery across a network is the kind of guarantee [Distributed Transactions](../../hld-building-blocks/distributed-transactions-saga.md) shows is extremely expensive to get right; at-least-once plus `client_msg_id` gets the same practical outcome for a fraction of the complexity |
| Large-group fan-out | Fan-out-on-write below a few hundred members, fan-out-on-read above | Always fan-out-on-write | Pushing to thousands of members on every single message doesn't scale the way pushing to a few hundred does — the same threshold-based hybrid this guide's [News Feed System](../news-feed-system/00-overview.md) case study uses for celebrity accounts |
| Receipt updates | Monotonic state transition (`status_rank` guard) | Last-write-wins overwrite | An out-of-order "delivered" ack arriving after "read" must never regress the visible status — the guard makes that structurally impossible rather than relying on client-side ordering |

## Load Handling

- **Peak-vs-average tolerance:** the system tolerates roughly 3x average load (the peak factor already used in Capacity Estimation) without shedding anything — ordinary horizontal scaling of the stateless Message Service tier and adding more Gateway instances.
- **Where backpressure kicks in first:** a regional outage recovery reconnect storm — a large fraction of 200M connections all reconnecting within seconds — hits the Connection Gateway tier hardest, not Message Service. New connection attempts are rate-limited per gateway instance rather than accepted unboundedly (cross-ref [Backpressure, Load Shedding & Bulkheads](../../scalability-resilience/backpressure-load-shedding.md)), and a client that's shed retries with jittered backoff rather than every client reconnecting in the same instant.
- **What gets shed under overload:** never a message itself — the durable write always happens regardless of load. What sheds under extreme pressure is presence-update fan-out and non-critical read-receipt delivery; a contact's "online" badge lagging by a few seconds is invisible next to a lost message.
- **Autoscaling lag:** gateway autoscaling reacts on a 1-3 minute horizon. A sudden reconnect storm's first wave is absorbed by existing headroom and per-instance connection rate limits, not by autoscaling reacting fast enough for the first ten seconds.
- **Load-test target:** sustain 1M new connection attempts/minute for 10 minutes without the Presence Registry's write rate exceeding provisioned capacity, and zero message loss throughout.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| Two devices, same user, different online states (phone reconnects while laptop is still connected) | Presence is stored per-`(user_id, device_id)`, not per-user; a contact sees "online" if *any* entry exists | A stale read for at most one heartbeat interval — never a correctness bug |
| Concurrent delivery-receipt writes for the same message (delivered/read acks arriving out of order, or from two of the recipient's devices) | `UPDATE delivery_receipts SET status='read' WHERE message_id=? AND status_rank < 3` (sent=1, delivered=2, read=3) — a monotonic guard, not a blind overwrite | An out-of-order "delivered" arriving after "read" is a no-op, never a regression |
| Message ordering within one conversation, sent from multiple devices near-simultaneously | The message ID itself is the ordering key (see Database Design); whichever write lands first in the conversation's ordered store wins the earlier slot | No "correct" wall-clock order is preserved beyond what actually got persisted first — clients render in persisted order, not send-time order |
| Two of a user's own devices sending `message.send` for the same logical message (a retry after a flaky connection) | Unique index on `(conversation_id, client_msg_id)`, the client-generated idempotency key | The second insert is a no-op, returning the first attempt's `message_id` rather than creating a duplicate |

## Scaling & Reliability

- **Horizontal scaling:** Message Service is stateless and scales by request rate, same as any stateless tier in this guide. Connection Gateway scales by *connection count*, not request rate — a fundamentally different capacity model that has to track concurrent sockets held, not requests/sec served.
- **Circuit breaker:** a push to a specific gateway instance that's become unreachable trips a breaker so Message Service doesn't pile up retries against a dead instance — the message, already durably written, simply falls back to the offline-delivery path instead of being retried indefinitely against a gateway that's gone.
- **Retries:** `message.send` is retried client-side with the same `client_msg_id`, safe because of the idempotency guarantee in the Concurrent-User table above; push delivery to a specific gateway is retried a small, bounded number of times before falling back to the offline queue.
- **Dead-letter queue:** an offline-queue entry that fails to deliver after repeated attempts (a permanently deleted account, for instance) lands in a DLQ rather than retrying forever against a recipient who will never reconnect.
- **Graceful degradation:** if the Presence Registry is fully down, the system defaults to treating every recipient as offline — messages still land safely via the durable write and get pulled on the next reconnect or poll; delivery just loses its low-latency push characteristic until the registry recovers, rather than failing outright.
- **Multi-region:** not built here, and worth naming as a real gap — a sender connected to a gateway in one region and a recipient connected to a gateway in another needs cross-region routing in the Presence Registry itself, which this design doesn't address.

## What you'd revisit as this grows

- **Group chats beyond a few hundred members** need the fan-out-on-read switch named in the Trade-offs table above — this design assumes that threshold hasn't been crossed; past it, the write-fan-out cost itself becomes the bottleneck.
- **Cross-region presence routing**, for a genuinely globally distributed user base rather than the single-region assumption this module makes.
- **End-to-end encryption**, which would change what "durable write" even means for the Message Store — the server can no longer inspect message bodies, which has direct consequences for content moderation and search, both deliberately scoped out of this module so each can be reasoned about on its own.
