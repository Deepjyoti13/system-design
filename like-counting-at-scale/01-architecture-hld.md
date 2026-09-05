# Module 01 — Architecture & High-Level Design

**Diagram for this module:** [Write Once, Count Everywhere](https://claude.ai/code/artifact/250b31c9-8819-4a51-a1ba-3c7899054f41)

## Requirements (stated, not guessed)

There's no live interviewer here, so these are stated explicitly rather than interviewed out — exactly what you'd do out loud if an interviewer waved a hand and said "assume whatever's reasonable."

**Functional:**
- A user can like a post (toggle on) and unlike it (toggle off).
- Anyone viewing a post sees an approximate like count that updates in near real time.
- A user viewing a post they've liked sees their own heart filled in — and that has to be *exact*, not approximate.

**Non-functional:**
- **Scale:** assume 500M DAU, ~20 likes given per user per day → ~10B likes/day, ~115,000 likes/sec average across the whole platform.
- **The number that actually matters — hot-key concentration:** a single viral post can receive up to 50,000 likes/sec at peak. This is the crux of the whole design; the aggregate QPS above is almost a distraction next to it.
- **Latency:** the toggle must confirm to the user in well under 150ms (p99). The *displayed* count is allowed to lag reality by a few seconds.
- **Availability:** liking a post must keep working even if the count-aggregation pipeline is fully down.

## Monolith vs. microservices

Pulled out as its own service (**Like Service**), not folded into a general "Post Service" monolith, for one concrete reason: this is the one code path in the whole platform that needs to survive a single post receiving 50,000 writes/sec, and it has a scaling and reliability profile (aggressive horizontal scaling, its own circuit breakers to Kafka, its own rate limiting) that would otherwise force the entire Post Service to be provisioned and operated for a worst case that applies to a tiny fraction of posts. The seam sits exactly at "toggle a like" / "read a count" — everything else about a post (caption, media, comments) stays wherever the rest of the platform already lives, and only talks to Like Service over its own API. If your platform is small enough that no single post has ever threatened to melt a database row, this split isn't worth the operational cost yet — say so out loud rather than defaulting to microservices because it sounds more sophisticated.

## Building blocks

| Block | Role |
|---|---|
| Load balancer | Spreads toggle/read traffic across Like Service instances |
| **Like Service** (stateless) | Owns the toggle and the read path; the only thing every other block exists to support |
| **Like Ledger** — sharded NoSQL, partition key = `post_id` | Exact per-(user, post) toggle state — the source of truth for "did I like this" |
| **Redis Cluster** — sharded hot counters | Fast, lock-free approximate count, updated on every toggle |
| **Kafka** — `like-events`, partitioned by `post_id` | Decouples the toggle's latency from the durable count |
| **Count Aggregator** | Batches events into infrequent, large writes instead of one write per like |
| **Posts DB** — sharded Postgres | Durable, reconciled `like_count`, and the fallback read path if Redis is down |

## Per-path walkthrough

**Toggle path (write)** — `Client → LB → Like Service → Like Ledger (conditional write) → Redis (INCR/DECR) → Kafka (publish, fire-and-forget)`. The ledger write and the Redis increment are both sub-10ms operations; the Kafka publish never blocks the response. Target: p99 < 150ms, dominated entirely by the ledger write.

**Read path (view a post)** — `Client → LB → Like Service → Redis (GET, sum shards) + Ledger (point lookup: did *I* like this)`. On a Redis miss, fall back to `Posts DB.like_count` and repopulate the cache. Target: p99 < 50ms on a cache hit (~99% of reads), because this runs on every single post render across the platform.

**Async aggregation path** — `Kafka → Count Aggregator (batch ~2s window, net delta per post_id) → Posts DB (one batched increment)`. This is the mechanism that turns potentially 50,000 individual writes/sec on one row into roughly one write every two seconds for that same row — the literal answer to "how do you avoid a billion row locks."

## Back-of-envelope math

- 10B likes/day ÷ 86,400s ≈ **115,000 likes/sec average**, platform-wide.
- **50,000 likes/sec on one post_id at peak** (stated assumption) — this is the number the design is actually built around.
- A naive `UPDATE posts SET like_count = like_count + 1 WHERE id = ?` at 50,000/sec on one row would serialize every writer behind that row's lock — even an in-memory DB measured in microseconds per lock acquisition caps out somewhere well south of that under real contention and connection overhead. Redis `INCR` sidesteps this entirely: it's a single-threaded, O(1), lock-free operation — the "lock" is implicit and never contended because Redis processes commands to one key one at a time internally, with no separate acquire/release/blocked-waiter machinery.
- Ledger storage: even a modest average of a few hundred likes per post, with a long tail of viral posts, puts total live (user, post) toggle rows in the low trillions after a few years at this DAU — at ~50 bytes/row, low hundreds of TB. This is why the ledger is sharded from day one, not something to "add sharding to later."

## Trade-offs

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Hot counter mechanism | Redis `INCR` | DB row `UPDATE ... +1` | `INCR` is O(1) and lock-free; a DB row increment serializes every concurrent writer on that exact row — the literal problem this system exists to avoid |
| Counter sharding | N sub-keys per post, summed on read | One Redis key per post | Redis Cluster still routes one key to one node/slot; an extremely hot single key becomes that one node's ceiling regardless of how many app servers you add |
| Durable count updates | Async, batched (~2s windows) | Synchronous write-through on every like | Writing every like straight to the durable, replicated store reintroduces the exact hot-row problem one layer down — the cache would be pointless |
| Toggle ledger storage | Sharded NoSQL, partition = `post_id` | Relational (as the URL-shortener project used) | Only query patterns are "does (user, post) exist" and "list likers" — both single-partition lookups, no joins. The URL shortener's reason for choosing SQL (joins, secondary-index analytics) doesn't apply here |
| Toggle → aggregator handoff | Kafka (partitioned log) | Direct API call from Like Service to Aggregator | The toggle must never block on the aggregator's availability or speed; a log also preserves per-post ordering for free, which the aggregator's batching needs |
| Displayed count consistency | Eventually consistent (lags up to ~2-5s) | Always exact | Exactness would require synchronous coordination across every concurrent liker of the same post — the very serialization this design exists to avoid. Note this does **not** extend to the toggle itself, which stays exact |

## Load handling

- **Peak-vs-average tolerance:** the platform-wide average (115K/sec) is comfortably absorbed by horizontally scaling Like Service and adding Kafka partitions — that's an ordinary capacity problem. The number that actually stresses the design is per-key: one post going from near-zero to 50,000 likes/sec within minutes. That's not solved by adding app servers at all; it's solved entirely by counter sharding (above) plus admission control (below).
- **Where backpressure kicks in first:** a per-user rate limit (token bucket in Redis, e.g. one toggle per post per 2 seconds) blunts double-tap spam and like-farm bots *before* they ever reach the ledger or the counter. Kafka itself is the buffer for the aggregation stage — if the Aggregator falls behind, events queue in Kafka rather than blocking or being dropped.
- **What gets shed under overload:** never the toggle write itself — that's correctness-critical. If a specific counter shard's Redis node is saturated, the Like Service can skip the synchronous `INCR` for that request and instead let the Kafka event alone carry the increment (applied a beat later by the Aggregator). The user's own heart still toggles instantly; only the *visible aggregate count* is what's allowed to lag further than usual.
- **Autoscaling lag:** Like Service autoscaling reacts on a 1-3 minute horizon. The seconds-scale gap before that kicks in is absorbed by existing headroom on already-provisioned Redis shards and Kafka partitions, not by autoscaling — autoscaling is for sustained growth, not for a spike that started ninety seconds ago.
- **Load-test target:** sustain 60,000 write ops/sec against a *single* `post_id` (one artificially hot key) with p99 toggle latency under 150ms and zero toggle write failures, for 10 minutes.

## Concurrent-user handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| Same user double-taps like/unlike on the same post (retry, or fast re-tap) | Ledger write is conditional/idempotent — `INSERT ... IF NOT EXISTS` keyed by `(post_id, user_id)` | An identical success response, not an error — it's a no-op, not a conflict |
| Two *different* users like the same post at the same instant (the actual "billion likes" scenario) | Nothing to resolve: Redis `INCR` is commutative and atomic per key, so concurrent callers never read-modify-write against each other | Both succeed independently — this is the case people instinctively reach for a lock on, and there's no lock because there's no race |
| A user rapidly flaps like → unlike → like within one aggregator batch window | The Aggregator applies a **net delta** per `(post_id)` per window, not a replay of every individual event in order | The ledger's current state is the single source of truth for "final" per-user state regardless of how many times it flapped; the durable count only needs to converge to match it eventually |
| Aggregator process crashes mid-batch | Kafka consumer offsets commit only after a successful durable write; the durable increment is idempotent per message offset | A redelivered batch after a crash doesn't double-apply — at-least-once delivery, exactly-once effect |

## Scaling & reliability

- **Horizontal scaling:** Like Service is stateless and scales behind the LB by request rate. Redis Cluster scales by adding nodes and resharding hash slots. Kafka scales by adding partitions (sized generously upfront, since changing partition count later reshuffles key-to-partition mapping).
- **Circuit breaker:** the Kafka publish is wrapped in a circuit breaker. If Kafka is unreachable, the toggle still completes (ledger write + Redis `INCR` both still succeed) — the request never fails for a reason that has nothing to do with the user's own action.
- **Retries:** transient Redis/ledger errors get up to 2 retries with exponential backoff + jitter, safe because the ledger write is already idempotent — capped so retries never blow the 150ms budget.
- **Dead-letter queue:** the Aggregator sends a batch to a DLQ topic after N failed apply attempts (e.g. a malformed event), rather than blocking that partition's entire ordered stream.
- **Graceful degradation:** if Redis Cluster is entirely down, both the read and toggle paths fall back to `Posts DB.like_count` directly — slower, and now exposed to the very row-contention problem this design exists to avoid, but the feature keeps functioning at reduced throughput rather than failing outright. This is explicitly the fallback of last resort, not a second normal mode.
- **Multi-region:** not built here — noted as a "revisit as this grows" item below, the same way the URL-shortener project treated it.

## What you'd revisit as this grows

- A fixed shard count per counter (say 16 sub-keys) works until a post goes *ultra*-viral — a global news event, a mega-celebrity post — past even that ceiling. A real system needs **dynamic** re-sharding: detecting a hot key and increasing its shard count on the fly, which is genuinely hard and worth naming as future work rather than solving here.
- The reconciliation job that re-derives an exact count from the ledger (to correct any drift the async pipeline accumulates) gets expensive as the ledger grows into the trillions of rows — eventually it needs to be incremental/checkpointed rather than a full scan.
- Cross-region replication of the ledger and the hot counters, if the platform serves multiple regions and wants low write latency everywhere rather than one write region.
