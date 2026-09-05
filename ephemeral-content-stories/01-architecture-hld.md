# Module 01 — Architecture & High-Level Design

**Diagram for this module:** [Ephemeral Stories — architecture diagram](https://claude.ai/code/artifact/7e6d2896-941f-4d93-8b50-43c5f24d1d07)

## Step 1: requirements

**Functional requirements:**
- Post a photo or short (≤15s) video, visible to your followers.
- A story stops being viewable exactly 24 hours after posting — permanently, for everyone including the poster.
- Followers see a "Story Tray": stories from people they follow, from the last 24 hours.
- The poster can see who viewed their story, in view order.

**Non-functional requirements (assumed, since there's no live interviewer to ask):**
- **Scale:** 500M DAU, ~2 stories posted per user per day → 1B stories/day. Each story is viewed by ~20 people on average → 20B views/day. Read:write ≈ 20:1.
- **Latency:** opening the Story Tray must feel instant — p99 < 150ms. This is the single highest-QPS endpoint in the whole app.
- **Availability:** viewing existing stories must keep working even if the view-count/analytics pipeline is degraded. Accepting *new* posts is less critical than serving reads that already exist.
- **Correctness (non-negotiable):** a story must never be servable after its 24 hours are up, even if some background cleanup job is late, stuck, or fully down. This is the requirement that shapes almost every decision below.

What falls out of just this list: because "never show an expired story" has to hold *regardless of what any batch job is doing*, expiry can't be a job that runs periodically and hopes to keep up — it has to be enforced at the moment of every single read. That's the idea the whole architecture is built around.

## Step 2: monolith vs. microservices

This system almost certainly lives inside a much larger social app, not standalone. Two things push toward splitting it into its own service rather than a module inside a monolith:
- **Differential scaling.** The Story Tray read path is the highest-QPS endpoint in the entire app; video transcoding is bursty, CPU-heavy background work. Bundling both into one deployable means scaling one for the other's sake.
- **Independent ownership.** A dedicated Stories team ships changes to posting/viewing without redeploying (or risking) the rest of the app.

The seam goes exactly where those two facts already put it: a **Story Service** (stateless, handles create/getTray/recordView) and a separate **Media Upload Service** (stateless, handles the actual upload and kicks off transcoding) — not one merged "Content Service," because upload/transcode and tray-read have almost nothing in common operationally.

## Step 3: the building blocks

- **API Gateway / Load Balancer** — routes to the two services below; nothing story-specific happens here.
- **Media Upload Service** — accepts the raw photo/video, pushes it to object storage, hands a `media_ref` to Story Service.
- **Story Service** — `create`, `getTray`, `recordView`. Stateless, horizontally scaled.
- **Redis** — two jobs: (a) the **visibility gate** — a key per story with a native 24-hour TTL, which is the actual source of truth for "is this story still alive," and (b) a short-TTL (30s) cache of each user's assembled Story Tray.
- **MySQL, primary + read replica** — `stories` and `story_views`, partitioned by day (see below).
- **Object Storage (S3) + CDN** — the media bytes themselves. Never touch the database.
- **Kafka** — event bus for `story.created` and `story.viewed`, each with its own consumer group (transcoding workers; viewer-list writer).

## Step 4: the expiry mechanism — the one idea this whole module exists to teach

The naive approach — a cron job doing `DELETE FROM stories WHERE expires_at < NOW()` — fails two ways at this scale: it's enormous write amplification against a billion-row table, and it's a **correctness** risk, not just a performance one — if the job is late, already-expired content stays visible, which this system's non-functional requirements explicitly forbid.

Instead:
- **The gate is Redis, not the SQL row.** At creation time, `SET story:{id}:visible 1 EX 86400`. Every read checks this key — if it's missing (expired or never existed), the story is gone, full stop, regardless of what the SQL table says. Redis's key expiry is a single atomic operation with no batch job involved — correctness by construction.
- **The SQL table is partitioned by `created_date` (one partition per day).** Physical cleanup is `ALTER TABLE stories DROP PARTITION p_20260101` — an **O(1)** metadata operation, not an O(n) row-by-row delete. This can lag by hours with zero user-visible effect, because Redis already hid the content the moment it expired.

## Step 5: the three paths

**Write path — posting a story**
`Client → LB → Media Upload Service → S3 (raw media)`, then `Media Upload Service → Story Service (media_ref) → MySQL primary (INSERT) + Redis (SET EX 24h) → Kafka (publish story.created)`.
The story is visible to viewers as soon as the *original* upload is in the CDN — transcoded, optimized variants swap in over the next few seconds via the async path, so posting never blocks on transcoding.

**Read path — opening the Story Tray**
`Client → LB → Story Service → Redis` (cached tray, 30s TTL) `→ on miss: MySQL replica (following-list + recent stories) → filter through Redis visibility gate → repopulate cache → Client`.
This is the 231,000-req/sec path (see math below), which is why it gets the cache and why the cache TTL is short — unlike the URL shortener's near-permanent mapping, a Story Tray changes the instant anyone you follow posts.

**Async path — views and transcoding**
`Story Service → Kafka (story.viewed) → Viewer-List Writer → MySQL (story_views) + Redis Set (SADD viewer_id, for dedup'd live counts)`, and separately `Kafka (story.created) → Transcoding Workers → S3 (optimized variants)`. Neither consumer is on the critical path of a view or a post.

## Back-of-envelope math

- **Writes:** 1B stories/day ≈ 11,600/sec average; call it ~35,000/sec at a 3x evening peak.
- **Reads:** 20B views/day ≈ 231,000/sec average — this number, not the write side, is what the architecture is built to survive.
- **Media storage:** unlike the URL shortener, this does *not* accumulate forever — content expires. ~4MB average per story (blended photo/video) × 1B/day ≈ 4PB/day of *hot* storage at any given time (roughly one day's worth is "live"), plus a rolling ~30-day cold/archival copy for abuse review (~120PB in cheap cold storage) before hard deletion.
- **Metadata:** the `stories` row (~200 bytes) × 1B/day × ~35 days retained (24h live + a 30-day audit window) ≈ 7TB — small and tractable, because unlike the media, metadata retention is a deliberate, bounded choice, not a byproduct of the requirements.

## Trade-offs, made explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Expiry mechanism | Redis native TTL as the visibility gate | Cron job scanning/deleting expired rows | The cron approach is both slower (huge write amplification at billion-row scale) and a correctness risk (a late job = visible expired content, which the requirements forbid outright) |
| Story Tray assembly | Fan-out-on-read (compute from the follow-list at view time, cache briefly) | Fan-out-on-write (push into every follower's tray at post time) | A story's visibility set is *your own* following list — small and bounded — unlike a celebrity's follower list in a news-feed fan-out problem, so on-read is cheap enough and avoids write-amplifying every post to every follower's cache entry |
| Event bus | Kafka | SQS | Three independent consumer groups (transcoding, viewer-list writer, analytics) need to read the *same* event streams independently; Kafka's per-consumer-group offsets fit that natively, SQS's competing-consumer model would need a queue per consumer |
| `stories` / `story_views` store | SQL (MySQL), partitioned by day | Pure KV/NoSQL (DynamoDB) | Partition-drop as O(1) bulk expiry is a first-class relational feature, and "list viewers of story X in order" is a plain range-indexed query — DynamoDB is a legitimate alternative (it even has native TTL), but then you'd have *two* independent expiry mechanisms (its TTL sweep + Redis) instead of one clean gate |
| Story Tray cache policy | Cache-aside, 30s TTL | Write-through | Unlike the near-immutable URL-shortener mapping, a tray changes the instant anyone you follow posts — a 30s staleness window is a fair trade for not push-invalidating every follower's cache entry on every post |
| Media availability | Serve original immediately, swap in transcoded variants async | Block story creation until transcoding finishes | Video transcoding can take several seconds; users expect a post to be live instantly, and the original is already good enough to view |

## Load handling

- **Tolerance:** designed to absorb ~3–5x average write QPS and ~5–8x average read QPS without shedding — view spikes (everyone opening the app when something happens) are the harsher case.
- **Where backpressure kicks in first:** the Media Upload Service rate-limits per-user upload attempts (stops a retry-looping client from flooding transcoding). The transcoding worker pool is the deliberate shock absorber — if it falls behind, stories are still fully visible (original-first, per the trade-off above), so a transcoding backlog is invisible to end users. The Story Tray read path never waits on transcoding at all.
- **Autoscaling:** Story Service and Media Upload Service scale on p99 latency + CPU, with roughly 1–2 minutes of reaction lag. In that window, the connection pool and Redis cache absorb the load; if Redis itself saturates, reads degrade to the MySQL replica (slower, still correct) rather than failing.
- **What sheds first:** new *uploads* get a 429 before *reads* are ever throttled — seeing what already exists is prioritized over accepting new writes, matching the availability requirement above.
- **Load-test target:** sustain 300k reads/sec + 40k writes/sec at Story Tray p99 < 150ms for 10 minutes, with the transcoding queue depth staying bounded (not growing) throughout.

## Concurrent-user handling

- **Two view events for the same viewer, near-simultaneously** (double-tap, client retry): a unique constraint on `story_views(story_id, viewer_id)` makes the second insert a no-op. The live view *count* is a Redis `SADD viewer_id` into a per-story Set rather than a raw counter, specifically so a duplicate view can never double-increment it — the count is `SCARD` of that set. The loser of the race sees nothing; their view is already recorded.
- **A read racing the expiry gate:** this isn't actually a race that needs a lock — Redis's `GET`/`EXISTS` on a single key with a native TTL is already atomic. Worth naming explicitly: not every apparent race needs a mechanism, because some primitives are already atomic by construction.
- **Two transcoding workers picking up the same `story.created` event** (Kafka's at-least-once delivery genuinely allows this on a consumer-group rebalance): the write of the transcoded-variant URL is an upsert keyed on `story_id`. Both workers produce a byte-identical result (a deterministic transform of the same source), so which one "wins" doesn't matter — the only cost is wasted compute, not a correctness bug.
- **Does Story Service ever run on more than one instance?** Yes — it's a stateless, horizontally scaled fleet by design (Step 2/3 above). That's exactly why every mechanism above is a DB constraint, an atomic single-key Redis operation, or an idempotency check — never an in-process mutex, which would silently do nothing across instances.

## What you'd revisit as this grows

- At true global scale, replicate the Redis visibility gate and read replicas into each region (same instinct as the URL shortener's own note on this).
- If a market's legal/regulatory retention window differs from the ~30-day default, partition by `(region, created_date)` instead of just `created_date`, so cleanup cadence can vary by region without touching the code.
- The Story Tray's fan-out-on-read assumption holds because following-lists are bounded — if a "subscribe to public figures with huge followings" feature were added, revisit this the same way a news-feed problem has to (see the separate, unrelated news-feed practice problem for that trade-off).
