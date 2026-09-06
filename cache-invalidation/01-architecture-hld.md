# Module 01 — Architecture & High-Level Design

**Diagram for this module:** [`diagrams/01-architecture.svg`](diagrams/01-architecture.svg)

## Requirements (stated, not guessed)

**Functional:**
- A write to the source of truth (a product price, a user's avatar, a homepage banner) must eventually make every cached copy — app-local memory, the shared Redis layer, the CDN edge — reflect the new value.
- Nothing downstream should ever be REQUIRED to actively poll for changes; a write should be able to push notice of itself out.

**Non-functional:**
- **Scale:** assume a platform serving ~2M cache reads/sec across all layers combined. Normal writes trigger a modest, steady trickle of invalidations.
- **The number that actually matters — burst fan-out:** a bulk operation (a mass re-price of 100K products, a botched deploy that forces a full cache bust) can produce on the order of 20,000 invalidation events in a few seconds. This is the case the design is built around; steady-state traffic is the easy part.
- **Latency:** a hot, user-visible change (a homepage banner, a price correction) should reach every cache layer within roughly 2–5 seconds of the write committing.
- **Availability:** normal reads and writes to the source of truth must keep working even if the entire invalidation pipeline is down — a stale cache is a correctness *degradation*, not an outage.

## Monolith vs. microservices

Cache invalidation isn't a user-facing service — it's a cross-cutting concern threaded through every service that caches anything. Folding it entirely into each service (each one hand-rolling its own purge logic) means the CDN-purge-API quirks, the retry/backoff policy, and the event schema all get reinvented per team. This design pulls out exactly one small piece as its own service — a **Purge Coordinator** — whose only job is talking to the CDN's purge API, retrying, and tracking propagation. Everything else (publishing an invalidation event, subscribing to one) is a thin library every service links against, not a service of its own. If your whole platform has one cache in front of one database, this split isn't worth standing up yet — say so rather than defaulting to it.

## Building blocks

| Block | Role |
|---|---|
| Source-of-truth DB | Where the write actually happens; writes an outbox row in the same transaction (see [The Transactional Outbox & CDC](../content/hld-building-blocks/transactional-outbox-cdc.md) — this system is a direct application of that pattern) |
| CDC relay / outbox reader | Turns each committed outbox row into one invalidation event |
| **`cache-invalidate` topic** (Kafka) | Fans one event out to every subscriber at once — see [Message Queues & Pub/Sub](../content/hld-building-blocks/message-queues-pubsub.md) |
| App-server local cache (in-process) | Subscribes directly; evicts/updates on its own instance the moment an event arrives |
| Redis Cluster (shared cache) | Subscribes the same way, or is bypassed entirely by versioned keys (below) |
| **Purge Coordinator** (small service) | Calls the CDN purge API for content that can't use versioned keys; retries, rate-limits itself, tracks completion |
| CDN edge network | Serves cached content close to users — see [CDN](../content/hld-building-blocks/cdn.md) |

## Per-path walkthrough

**Write path** — `Client → App Server → DB (write + outbox row, one transaction) → CDC relay → cache-invalidate topic → { app-server local caches, Redis } apply immediately`. The write itself never waits on any of this — cross-ref the outbox pattern's whole point: the atomicity only ever has to span the DB, not the DB plus a broker plus a CDN.

**Versioned-key path (no active purge at all)** — for content addressable by a version or content hash (a product's rendered page, a static asset), the cache KEY itself encodes the version: `product:123:v7`. A price change bumps the version; the new page is `product:123:v8` — automatically a cache miss everywhere, on every layer, with no propagation race and nothing to broadcast. The old `v7` entries simply age out on their existing TTL; nothing has to actively find and delete them. This is the preferred mechanism wherever it applies.

**Active-purge path (where versioning doesn't fit)** — for the cases that don't have a clean "just don't reuse the key" answer (clearing one user's session cache, a hard takedown of a specific cached response), the Purge Coordinator calls the CDN's purge API directly, tracked and retried independently of the fast in-process/Redis path above.

## Back-of-envelope math

- Normal operation: a handful of invalidation events per second, dwarfed by the 2M reads/sec the caches are serving — invalidation traffic is not the bottleneck in steady state.
- Burst case: **20,000 invalidation events in ~5 seconds** (the stated assumption) fanned out over Kafka to, say, 200+ app-server instances plus a Redis cluster — each subscriber sees the full 20,000-event burst, not 20,000 ÷ 200. Kafka partitioned appropriately absorbs this as a straightforward consume-rate problem, not a new one.
- The CDN purge API is the actual constraint during a burst: most providers rate-limit purge calls (low hundreds/sec is typical) — 20,000 events needing an active purge would take minutes to fully drain against that ceiling, which is exactly why versioned keys (no purge call needed at all) are the preferred path, not a nice-to-have.

## Trade-offs

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Primary invalidation mechanism | Versioned/hashed cache keys | Active purge (broadcast + acknowledge) | A version bump is just "stop reusing the old key" — no propagation race, no purge-API rate limit. Only works where the *key itself* can encode a version; a fixed logical key like "this user's session" has nowhere to put one |
| Purge broadcast mechanism | Pub/sub (Kafka topic) | Every cache layer polls the DB for a `last_modified` timestamp | Pub/sub pushes the event once and fans out to every subscriber in parallel, sub-second. Polling multiplies read load on the source DB by however many cache layers exist, and adds up to a full poll-interval of staleness |
| CDN invalidation | Explicit purge API call, tracked by the Purge Coordinator | Wait out the existing TTL | A factual correction or a takedown can't tolerate waiting out a cache TTL; but purge calls cost real rate-limit budget and real propagation time, so this is reserved for content versioning can't cover |
| Local (app-server in-memory) cache invalidation | Subscribe directly to the same event bus | TTL-only, no active push | 200+ instances relying only on TTL expiry means up to a full TTL window where different app servers visibly disagree with each other. The event bus gets every instance the same message in the same fan-out |

## Load handling

- **Peak-vs-average tolerance:** steady-state invalidation traffic is trivial next to the 2M reads/sec the caches serve; the number that stresses this design is a burst — a mass re-price, a forced full cache bust — producing tens of thousands of events in seconds.
- **Where backpressure kicks in first:** the Kafka topic itself buffers the burst for the fast subscribers (local caches, Redis); the Purge Coordinator explicitly rate-limits its own outbound calls to the CDN's purge API (cross-ref [Rate Limiting](../content/hld-building-blocks/rate-limiting.md)) so a burst queues inside the Coordinator rather than getting the whole account throttled or blocked by the provider.
- **What gets shed under overload:** never the versioned-key path — it's passive and costs nothing extra under load by construction. What can lag is the *active* CDN purge queue: during a mass-invalidation burst, some non-versioned content stays stale at the CDN edge a little longer than the 2–5s target, an explicit, bounded degradation — not silently dropped, just queued behind the rate limit.
- **Autoscaling lag:** irrelevant to the hot path here — Kafka's own buffering, not new consumer capacity, is what survives the first seconds of a burst; more consumers only help once the burst is sustained for minutes, not seconds.
- **Load-test target:** sustain a burst of 20,000 invalidation events within 5 seconds, across all subscriber layers, with 99% of app-server-local caches updated within 500ms, and CDN purge calls queued (never dropped) against the provider's real rate limit, fully drained within 60s.

## Concurrent-user handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| A cache-miss read is about to write a (now-stale) value back into cache exactly as an invalidation for that same key arrives | Every cached entry and every invalidation event carries a version. Before a repopulation write lands, its version is compared against the last-seen invalidation watermark for that key; a repopulation with an older version is a no-op, not applied | The stale repopulation silently does nothing; the next read simply misses again and re-fetches, this time picking up the current value |
| Two concurrent writers produce two different new versions of the same key | The version-compare above is a strict greater-than check, so whichever version is numerically/temporally later always wins the cache slot, regardless of which network message physically arrives first | The earlier version's cache write is discarded the same way a stale repopulation is — same mechanism, no special case needed |
| An app-server instance is mid-restart (resubscribing to the invalidation topic) exactly as an event fires | Kafka consumer offsets are tracked per-instance; a restarting instance resumes from its last committed offset rather than "now," so it replays anything it missed instead of silently skipping it | No reader sees a permanently-stale local cache from a missed event — it's caught up on reconnect, just slightly delayed |

## Scaling & reliability

- **Horizontal scaling:** the outbox/CDC relay and the Kafka topic scale independently of the app-server fleet; adding app-server instances just adds subscribers, not load on the invalidation pipeline itself.
- **Circuit breaker:** the Purge Coordinator's calls to the CDN API are wrapped in a circuit breaker (cross-ref [Circuit Breakers & Retries](../content/scalability-resilience/circuit-breakers-retries.md)) — a CDN outage queues purges rather than piling up failed retries against a provider that's already down.
- **Retries:** CDN purge calls retry with exponential backoff + jitter; safe because a repeated purge of the same key is idempotent by definition.
- **Graceful degradation:** if the entire invalidation pipeline is down, reads and writes to the source of truth are completely unaffected — caches simply keep serving whatever they last had until TTL expiry, a slower but still-correct-eventually fallback, never a hard failure.
- **Multi-region:** not built here — noted below as a "revisit as this grows" item, same as this project's other worked examples.

## What you'd revisit as this grows

- A wildcard/category-level invalidation ("clear every cached page under this category") doesn't fit the single-key versioning model cleanly and would need its own design — named here as a real gap, not solved.
- Cross-region propagation of the invalidation topic itself, if the platform serves multiple regions and each has its own local cache tier.
- The Purge Coordinator's own availability becomes a second-order dependency once active purge is relied on heavily; today it's explicitly the fallback path, not the primary one, which is what keeps this acceptable.
