# Module 01 — Architecture & High-Level Design

**Diagram for this module:** [Architecture diagram](https://claude.ai/code/artifact/ccf0758e-704a-4f0e-a628-2569b3e38103)

## Requirements

**Functional:**
- Show a contact as "online now" or "last seen at T."
- Deliver per-message read receipts (sent → delivered → read) to the sender, per participant.
- Reflect both of the above on a live screen within a couple of seconds, with no manual refresh.

**Non-functional (assumptions, stated explicitly — no live interviewer to ask):**
- Scale: 500M DAU, ~30% concurrently connected at peak → 150M concurrent persistent connections.
- Average user has ~150 contacts who might be watching their presence at any moment.
- 50B messages/day system-wide (existing messaging system this feature hooks into).
- Latency: a presence change should be visible to a subscribed viewer within ~2s; a read receipt should reach the sender within ~1s of the read action.
- Consistency: presence may lag by a couple of seconds (soft real-time, self-healing) — read state must **never regress** (a message already shown as "read" can never flip back to "delivered").

Notice what these numbers already decide: 150M *concurrent* connections is the number that has to be held open cheaply, not served per-request — that's why the architecture below is built around long-lived connections, not a stateless request/response fleet.

## Monolith vs. microservices — the call, and why

**Three separate deployable services**, not one:

1. **Connection Gateway** — owns live sockets. Scales with *open connections* (memory-bound).
2. **Presence Service** — owns online/offline state. Scales with *heartbeat volume*, and its data is disposable (a crash loses nothing that a 30-second TTL doesn't already assume can go stale).
3. **Read Receipt Service** — owns the read-state machine. Scales with *message volume*, and its data must never be lost or go backward.

The seam is forced by the requirements, not a default preference for "microservices": presence tolerates loss and needs no durability at all, while receipts must never regress — coupling them into one service means the stricter requirement (receipts) drags durability machinery into the path of the looser one (presence), and the looser one's "just let it crash, TTL handles it" simplicity can't be exploited if it shares a deploy and a datastore with something that can't afford to crash carelessly. Each service also scales along a different axis (open sockets vs. heartbeat rate vs. message rate) — bundling them means over-provisioning two of the three every time you scale for the third.

## Building blocks

Only the ones the requirements above actually justify:

- **Load balancer / connection router** — routes an incoming connection to one gateway node and keeps it sticky for the session.
- **Gateway fleet** — stateful WebSocket-holding servers; each holds a shard of the 150M concurrent connections.
- **Pub/Sub bus (Redis Pub/Sub)** — lets any gateway node broadcast an event once and have it delivered to whichever other node(s) hold a subscribed connection, without gateway-to-gateway discovery.
- **Presence Service + Redis Cluster** — ephemeral online/offline state, TTL-based.
- **Kafka (receipts topic, partitioned by conversation_id)** — durable hand-off so the real-time push path never blocks on a database write.
- **Read Receipt Service + sharded Postgres** — durable, monotonic read-state storage.

No cache-aside layer, no CDN, no object store — none of them are justified by this feature (media storage for the messages themselves is a different system's concern).

## The worked design

Open the [architecture diagram](https://claude.ai/code/artifact/ccf0758e-704a-4f0e-a628-2569b3e38103). Every edge is labeled with its actual mechanism, and every one of those labels is a deliberate choice, not a default:

**Presence heartbeat path**
`Client —WebSocket (persistent)→ Gateway` — the client sends a lightweight heartbeat every 15s. Gateway refreshes `SETEX user:{id}:online 30 1` in Redis (TTL = 2× the heartbeat interval, so one missed beat doesn't flip someone offline). On a state *transition* (not every heartbeat), the gateway publishes to the Pub/Sub bus; any gateway node holding a subscribed viewer's socket pushes the update straight through. Target: visible within ~2s (heartbeat cadence + sub-second pub/sub hop).

**Disconnect path**
A clean TCP close publishes "offline" immediately. A *silent* disconnect (phone loses signal, app is killed) has no clean-close event at all — the Redis key's TTL expiring is the correctness backstop. Worst case: up to 30s of showing "online" after a silent drop. This is a deliberate, named trade-off, not an oversight.

**Read receipt path**
`Client —mark_read(conversationId, messageId)→ Gateway`. The gateway does two things **in parallel**:
1. Publishes the event to the Pub/Sub bus immediately (fast path) — the sender's open connection (if any) sees the checkmark turn blue in ~150–300ms.
2. Enqueues the same event onto Kafka, partitioned by `conversation_id` (durable path) — the Read Receipt Service consumes it and writes `last_read_message_id` to Postgres with a **conditional** update (`WHERE last_read_message_id < :new_id`), so it can never move the marker backward regardless of retries or out-of-order delivery.

**Cold/reconnect path**
If the sender was offline when the fast-path event fired, the durable Postgres row is the source of truth on next connect — the client just asks for current receipt state instead of relying on having caught the pub/sub event live.

## Back-of-envelope math

- 150M peak concurrent connections. A tuned event-loop gateway node holds ~65k idle WebSocket connections comfortably → **≈2,300 nodes** at peak, provisioned to ~2,500 with headroom.
- Heartbeats: 150M ÷ 15s ≈ **10M/sec system-wide** ≈ 4,000/sec per node — trivial load per node.
- Read receipts: 50B messages/day ≈ 580k/sec average, ~2M/sec at a 3.5× peak multiplier — well inside what a partitioned Kafka cluster handles routinely (single clusters commonly sustain 1M+ msgs/sec).
- Presence storage: 500M users × ~50 bytes ≈ **25GB** in Redis — a handful of nodes.
- Receipt storage: 500M users × ~20 active conversations × ~40 bytes/row ≈ **~400GB** — comfortably shardable across 50–100 Postgres shards.

## Trade-offs, made explicit

| Decision | Choice made | Alternative | Why |
|---|---|---|---|
| Presence transport | WebSocket (persistent) | Short-poll every few seconds | At 150M concurrent users, repeated poll requests mean constant connection setup/TLS handshake overhead that dwarfs the cost of one held-open connection per user. |
| Presence storage | Redis key + TTL | Durable table + explicit heartbeat writes + reaper job | Presence is soft-state by requirement (can be stale for seconds); a TTL key is self-healing on crash with zero cleanup code — a DB row needs an explicit expiry job to get the same property. |
| Presence fan-out | Pub/Sub | Direct gateway-to-gateway RPC | A publishing node has no way to know which of ~2,500 peer nodes holds a given viewer's socket; pub/sub turns an O(N) discovery problem into "publish once, subscribers receive." |
| Receipt durability timing | Async Kafka write, in parallel with an immediate in-memory push | Synchronous DB write before pushing to the peer | The reader doesn't need the *durable* write to finish before the *sender* sees the checkmark; blocking the real-time path on a DB write reintroduces the tail latency the persistent connection was built to avoid. |
| Read-marker consistency | Conditional UPDATE, compares message IDs (monotonic) | Last-write-wins by timestamp | Client clock skew (a phone with a wrong clock) could move a timestamp-based marker backward; message IDs are already strictly ordered per conversation, so comparing them is skew-proof. |
| Presence data model | One key per user | One entry per (user, viewer) pair | A single `user:{id}:online` key scales with users (500M keys); a per-viewer model would scale with the much larger edge count (users × contacts). |

## Load handling

- **Peak-vs-average**: sizing above already targets *peak* concurrency (150M), not average — the fleet carries an explicit **20% headroom** (≈3,000 nodes provisioned against the ≈2,500-node peak estimate) to absorb a sudden regional spike (e.g. a viral moment) that outruns autoscaling.
- **Backpressure/shedding order** as load rises: (1) the heartbeat interval degrades first — extend 15s → 30s under load, halving heartbeat traffic at the cost of slower offline detection, invisible to most users; (2) the load balancer stops routing *new* connections to a gateway node once it's near its connection cap (existing connections are untouched); (3) only as a last resort does the Kafka receipt topic apply producer backpressure — this delays receipt *durability*, never message delivery, because the two paths are already decoupled.
- **Autoscaling lag**: gateway nodes are stateful — a freshly launched node adds capacity for *new* connections but does nothing for load already concentrated on existing ones. The 20%-headroom fleet exists specifically to cover the 3–5 minute gap before a new node is warm and receiving LB traffic.
- **Load-test target**: sustain 150M concurrent idle connections, 10M heartbeats/sec, 2M read-receipt events/sec, P99 presence fan-out < 2s and P99 read-receipt delivery < 1s, for a sustained 30-minute window with connection drop rate under 0.1%/min.

## Concurrent-user handling

- **Race: the same user has two devices open (phone + web), both heartbeating independently.** Not actually a conflict — `SETEX` is last-writer-wins on a TTL key, and "online" from *either* device is the correct answer (the key only needs to answer "is at least one connection alive," never "which device").
- **Race: two devices for the same user send overlapping `mark_read` calls, delivered out of order by network jitter** (phone reads up to message 500; web tab's earlier read of message 480 arrives after, due to delay). Resolved by the conditional `UPDATE ... WHERE last_read_message_id < :new_id` — Postgres's row lock during that single atomic statement serializes the two writers, and the loser's `UPDATE` simply matches zero rows: a silent no-op, not an error, because the winner's higher watermark already implies the loser's read state.
- **Race: "delivered" and "read" events for the same message arrive out of order** (a queue rebalance delays the earlier "delivered" event behind the later "read" event). Resolved by treating status as an ordinal (`sent=0 < delivered=1 < read=2`) and writing `status = GREATEST(status, :new_status)` — order of arrival stops mattering.

## What you'd revisit as this grows

- The ~2,500-node stateful gateway fleet is the single biggest operational cost at this scale. The next evolution is making gateway nodes *stateless* — hold only the raw socket, and push all "who is this connection" lookups into a shared layer — so nodes can be recycled without a client-visible reconnect storm.
- A single Kafka topic partitioned by `conversation_id` is a hot-partition risk for one extremely active group conversation; worth a composite partition key once a specific hot conversation is identified (the sibling "Counting a Billion Likes" design in this project hits the same hot-key shape from a different angle).
- This design assumes a single region. Multi-region would replicate Redis/Kafka per-region and cross-region-replicate only the durable Postgres layer — the ephemeral Redis presence layer has no reason to leave its region.
