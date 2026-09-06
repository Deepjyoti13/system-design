# Module 01 — Architecture & High-Level Design

![Fan-out-on-write for normal posts vs. fan-out-on-read for celebrity posts, merged at read time](diagrams/hld.svg)

## Monolith vs. microservices

Feed generation is pulled into its own service, not folded into a general Post/Timeline monolith, because it has a load profile no other part of a social platform shares: write amplification. A normal `POST /posts` call is one row insert from the poster's point of view — but from the *system's* point of view, publishing from a well-connected account can mean fanning that single write out into millions of downstream writes, entirely decoupled from how expensive the original post itself was to create. Bundling that fan-out work into the same service that also handles posting, commenting, and profile edits would mean provisioning the whole monolith for the fan-out worker pool's bursty, follower-count-driven load — including code paths that have nothing to do with feed delivery.

The celebrity-account read path sharpens this further: fetching a handful of oversized authors' recent posts at read time (`PullMerge`) is a fundamentally different operation — read-heavy, latency-sensitive, hit on every single feed load — from a background job appending rows into millions of feed caches. Splitting Feed Assembly (the read-time service) from the Fan-out worker pool (the write-time service) means each can be scaled and reasoned about against its own load shape, rather than one service somehow being provisioned simultaneously for "p99 under 300ms on every read" and "absorb a bursty pile of fan-out jobs after a viral post." If a platform is small enough that no single account's follower count has ever threatened to dominate a feed refresh, this split isn't buying anything yet — the trigger is how lopsided the follower-count distribution actually is, not scale alone.

**The central decision: fan-out-on-write vs. fan-out-on-read.**

- **Fan-out-on-write (push).** At post time, write the new post's ID into every follower's precomputed feed (a per-user list in a fast store, e.g. Redis). Reads are then trivial — just read your own precomputed list. The cost lands entirely on the write path, and it lands *per follower*: a celebrity's 10M followers means 10M writes for one post, most of them for followers who won't open the app for hours, if today at all.
- **Fan-out-on-read (pull).** Do nothing at post time. At read time, query the recent posts of everyone the requesting user follows (up to 300 people) and merge them on the spot. No wasted writes for followers who never look — but every single feed load now pays the cost of up to 300 queries and a merge, and at 50,000 reads/sec that's the expensive path instead.

**The hybrid that real systems use:** pick the path *per author*, by follower count. Below a threshold (e.g. 100K followers), fan-out-on-write — the write cost is bounded and reads stay cheap. Above the threshold, skip fan-out entirely; a celebrity's posts are instead fetched at read time and merged into the requester's precomputed feed for just that slice. A feed read becomes "read my precomputed list, then merge in any posts from accounts I follow that were too big to fan out" — cheap in the common case, and the one expensive celebrity post is now paid for lazily, once per reader who actually asks, not 10 million times upfront.

## Building blocks

- **Post Service** — accepts a new post, writes it durably, and decides (by the author's follower count) whether to enqueue a fan-out job or do nothing further.
- **Fan-out worker pool** — consumes fan-out jobs (cross-ref [Message Queues & Pub/Sub](../../hld-building-blocks/message-queues-pubsub.md)), writes the new post ID into each follower's feed cache. Only ever handles sub-threshold authors, which is what keeps its job small and boundable.
- **Feed Cache** — a per-user precomputed list of recent post IDs (Redis), the thing `GET /feed` reads from directly for the fan-out-on-write portion.
- **Feed Assembly / merge step** — at read time, reads the requester's Feed Cache, checks which of their followees are over-threshold, fetches those authors' recent posts directly, and merges both into one ranked page.
- **Feed Ranking** — chronological by default; an engagement-weighted ranking is a real refinement most production feeds add, but doesn't change anything about the fan-out decision above, so it's out of scope for the boxes here.

## Per-path walkthrough

**Post path, normal account (push)** — `Client → LB → Post Service (write post, SAME transaction as accepting the request) → FanoutQueue.enqueue (one job) → [async] Fan-out worker (SADD post_id into every follower's Feed Cache)`. The client gets a response the instant the post is durably written; delivery to followers trails behind by however long the queue takes to drain, which the requirements explicitly tolerate.

**Post path, celebrity account (pull)** — `Client → LB → Post Service (write post) → done`. No fan-out job is ever enqueued for an over-threshold author — the entire cost of delivering this post to its followers is deferred, in full, to the next bullet.

**Read path — feed assembly** — `Client → LB → Feed Assembly (read requester's Feed Cache) + (fetch recent posts from requester's oversized followees, if any) → merge_by_timestamp → paginated response`. This is the path that runs 50,000 times/sec, so it's the one built to be cheap by construction: a Feed Cache read plus, at most, a handful of extra queries for however many celebrities this specific reader follows — never all 300 followees, regardless of how many of them post.

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Primary delivery strategy | Threshold-based hybrid (push below threshold, pull above) | One uniform strategy for every author | Uniform push means a viral celebrity post costs 10M writes upfront; uniform pull means every normal-sized account's read pays a ~300-way merge on every load — the hybrid gives each case the strategy it actually needs |
| Feed freshness | Precomputed feed lagging by a few seconds (async fan-out) | Always-live query, assembled fresh on every read | Module 00 explicitly allows "eventually, within a few seconds" — paying for synchronous fan-out on every post would trade a rare, harmless inconvenience for guaranteed extra latency on every single post |
| Feed ranking | Chronological merge, with ranking layered on top at the same step | Bake a ranking model into the fan-out decision itself | Keeps the push/pull decision orthogonal to product ranking changes — ranking can evolve independently of the delivery mechanism that gathers candidates |
| Follower-list read at fan-out time | Snapshot semantics (accept staleness on an unfollow race) | Lock the follower list for the duration of the fan-out job | Locking a list that can have millions of entries, for as long as a fan-out job takes to run, would serialize follow/unfollow behind fan-out — a wildly disproportionate cost for a race that's harmless when it happens |
| `feed_items` retention | Per-user list capped and pruned (e.g. most recent 1,000) | Keep the full, unbounded history per user | Pagination realistically never goes back more than a few pages; an unbounded list grows storage and cache-memory cost for data nobody will ever page to |

## Load Handling

- **Peak-vs-average tolerance:** feed reads dominate (50:1), and both Feed Assembly and Feed Cache are stateless/horizontally scalable, so an ordinary 3x traffic peak is absorbed by adding instances, the same as any read-heavy tier in this guide. The defining spike here isn't traffic volume at all — it's a single celebrity post's read fan-in, which by design never touches the write path (see the central decision above).
- **Where backpressure kicks in first:** the fan-out worker pool, when many sub-threshold authors post in a short window (a world event triggering a wave of ordinary posts, not one viral post). Its queue is the pressure valve — a growing backlog delays *when* a post appears in followers' feeds by a few extra seconds, which the requirements explicitly tolerate, rather than failing the post itself.
- **What gets shed under overload:** never the post write, and never a feed read. What can lag: how quickly a fan-out job completes. [Backpressure, Load Shedding & Bulkheads](../../scalability-resilience/backpressure-load-shedding.md)'s oldest-first shedding applies to the fan-out queue specifically — a job for a post from 20 seconds ago is less urgent to finish than one from just now, since both are already past the point of feeling "instant" to the poster.
- **The real ceiling is per-reader, not platform-wide:** `PullMerge`'s cost is bounded by how many *oversized* accounts one user follows, not by total system load — a user follows at most a handful of celebrities out of their ~300 followees, so a feed read merges a small, bounded number of live queries regardless of how many other users are reading their own feeds concurrently. This is what keeps the read path's cost predictable even under a platform-wide traffic spike.
- **Load-test target:** sustain 50,000 feed reads/sec at p99 < 300ms while simultaneously processing 5,000 fan-out jobs/sec; separately, verify a single synthetic post to a 10M-follower account generates zero fan-out jobs — confirming the threshold routing holds under real load, not just in isolation.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| A user unfollows someone while that person's post is mid-fan-out | The follower list read at fan-out time is a snapshot, not a live constraint — an unfollow landing after the snapshot was taken doesn't retroactively pull the post back out | A harmless, one-time staleness: the post briefly appears in the (now ex-)follower's feed once, not a repeating bug |
| A fan-out worker crashes mid-job and its retry re-delivers the same post | The feed cache write is a set-add (`SADD`-style) — adding the same `post_id` twice is a no-op, not a duplicate entry | The follower's feed shows the post exactly once, regardless of how many times the job was retried |
| Two posts from the same author land in different followers' feeds out of order (parallel fan-out workers, no guaranteed completion order) | The feed is sorted by the post's own creation timestamp/ID at *read* time, not by insertion order into the feed cache | A consistently, correctly-ordered feed regardless of which worker's write landed first — ordering is a property of assembly, not of delivery |
| A like-count read races a concurrent like-count write | The displayed count is [BASE](../../database-design/acid-vs-base.md), not ACID — an eventually-consistent, cached counter | A reader might see a count a few writes behind, in exchange for a feed read that's never blocked by a concurrent like |

## Scaling & Reliability

- **Horizontal scaling:** Post Service, Feed Assembly, and the fan-out worker pool are all stateless and scale by request/job rate; Feed Cache scales as a sharded key-value store, partitioned by `user_id` (matching Database Design).
- **Circuit breaker:** fan-out workers' writes into Feed Cache are wrapped in a circuit breaker (cross-ref [Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md)) — if the cache tier degrades, the breaker trips and jobs back off rather than hammering an already-struggling store with retries that make things worse.
- **Retries:** fan-out jobs retry on transient failure, safe only because the cache write is idempotent (the set-add semantics above) — a retry after a partial failure never produces a duplicate feed entry.
- **Dead-letter queue:** a fan-out job that fails repeatedly (a malformed event, a permanently unreachable shard) lands in a DLQ rather than blocking that queue partition's other, unrelated jobs indefinitely.
- **Graceful degradation:** if Feed Cache is down, Feed Assembly falls back to a live query against `posts` for a user's followees directly — the same mechanism `PullMerge` already uses for oversized accounts — slower and more expensive, but the feed still loads instead of erroring, at reduced throughput until the cache recovers.
- **Multi-region:** not built here — see "what you'd revisit" below.

## What you'd revisit as this grows

- **Dynamic, per-author thresholds instead of one fixed follower-count cutoff.** A fixed threshold treats a 99,999-follower account and a 100,001-follower account identically to their respective strategies despite being nearly the same size — a more mature system might use a smoother cost model (recent posting frequency × follower count) rather than a single cliff-edge number.
- **Multi-region feed caches.** A single-region Feed Cache is a single point of latency (and failure) for a global user base; real platforms replicate feed data close to where users actually read it, with the same kind of active-active complexity this guide's [Payments System](../payments-system/01-architecture-hld.md) names as future work for its own ledger.
- **Ranking beyond chronological.** This module deliberately scopes ranking out (see Building Blocks above); a production feed's actual hard problem is often the ranking model, not the delivery mechanism covered here.
- **Fan-out worker pool auto-scaling tied to posting velocity, not just queue depth.** A queue-depth-only trigger reacts one step behind a sudden wave of posts (a world event); scaling ahead of a detected posting-rate spike would keep fan-out latency flatter through the transition.
