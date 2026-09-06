# Module 01 — Architecture & High-Level Design

![Query scatter-gather across term-partitioned index shards, merged and ranked before returning](diagrams/hld.svg)

## Monolith vs. microservices

Crawling, indexing, and query-serving are pulled apart into separate services, not for any regulatory reason but because their load profiles and scaling axes are almost completely uncorrelated. Crawling is throughput-bound and delay-tolerant — a page fetched a few minutes late is invisible to everyone. Indexing is a background batch/streaming job with its own CPU-heavy tokenization-and-postings-building profile. Query-serving is the one latency-critical, user-facing path (p99 under 200ms) that can never be blocked or slowed down by a crawl-rate spike or a large reindex running elsewhere. Folding all three into one monolith would mean a slow reindex job competing for the same shared resources as a live query — provisioning query-serving to survive that noisy neighbor would be paying a tax the natural queue boundary between crawling, indexing, and serving already avoids (Module 00's Approach Walkthrough already names this: "three stages that run at very different speeds and don't block each other").

The three tiers also scale on entirely different axes: the query-serving tier scales with query QPS, the crawler scales with politeness-limited per-host fetch throughput (a network/host-bound ceiling, not a CPU-bound one), and the indexer scales with document-ingestion rate. Provisioning them as one deployable unit would mean over- or under-provisioning at least one of those axes at any given moment. If your system is small enough that a nightly batch reindex barely dents query latency, this split isn't buying you much yet — the three stages could plausibly run as modules within one deployable, still logically separated by the same queue boundary, without the operational cost of three independently-deployed services.

## Building blocks

| Block | Role |
|---|---|
| **Crawler tier** | Continuously fetches pages, politeness-limited and deduplicated — cross-ref [Web Crawler](../web-crawler/README.md) for the frontier mechanics, not re-derived here |
| **Indexer** | Consumes crawled pages off a queue, tokenizes them, and builds inverted-index postings — entirely off the query-serving path |
| **Query Coordinator** (stateless) | Tokenizes an incoming query, fans out to the shards owning its terms, merges and ranks the partial results |
| **Index Shards** (term-hash partitioned) | Each owns one slice of the inverted index; answers with its own locally-ranked subset of matching documents |
| **Ranking signals** | An offline authority score (PageRank-style, precomputed) stored alongside postings, combined at query time with an online relevance score (TF-IDF/BM25) |
| **Query-result cache** | Absorbs repeat and near-duplicate queries before they ever reach a shard |

## Per-path walkthrough

**Crawl-and-index path (write)** — `Crawler (politeness-limited fetch) → Queue → Indexer (tokenize, build postings) → Shard (new immutable segment, atomic pointer swap)`. Runs continuously, entirely decoupled from query-serving; a backlog here shows up only as slightly staler results, never as a slow or failed query.

**Query path (read)** — `Client → Query Coordinator (tokenize) → fan-out to shards owning matched terms (parallel, bounded timeout) → per-shard local ranked results → k-way merge → Client`. This is the path the entire 200ms budget applies to, and every other decision below — caching, partial-result tolerance, term-partitioning — exists to protect it.

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Index shard key | Term-hash partitioning | Document partitioning | A multi-term query only fans out to the shards owning ITS terms; document-partitioning would force every query to touch every shard, since any shard might hold a matching document |
| Slow-shard handling | Bounded per-shard timeout + partial-result merge | Wait for every shard, always return complete results | A complete-but-late result loses against a 200ms budget; a fast, slightly-incomplete one wins — this system explicitly favors latency over completeness |
| Indexing cadence | Async, off a queue (hours-scale staleness acceptable) | Synchronous write-through indexing on every crawl | Blocking a crawl on a full reindex would cap crawl throughput at indexing speed; decoupling lets each scale on its own axis |
| Authority score computation | Offline batch (PageRank-style), precomputed | Computed live at query time | The link graph barely changes page-to-page; recomputing it per-query would be pure waste on the single most latency-sensitive path in the system |
| Query-result caching | Cache in front of ranking/merge | No caching, always scatter-gather | Real query traffic skews heavily toward a small set of popular/trending queries; caching absorbs a large share of peak load before it ever reaches a shard |

## Load Handling

- **Peak-vs-average tolerance:** the 3x peak factor (~300,000/sec) is itself dominated by the shard fan-out multiplier, not the raw query count — Capacity Estimation's 30M shard-ops/sec figure is the number that actually matters. Coordinators and shard-replica capacity both scale horizontally and statelessly.
- **Where backpressure kicks in first:** the per-shard bounded timeout — a shard slower than its budget contributes nothing to the merge rather than holding up the entire query.
- **What gets shed under overload:** completeness, never latency. A partial-but-fast result (missing one slow shard's contribution) is explicitly preferred over blocking for a complete one. Non-critical enrichment (spelling suggestions, "related searches") can be skipped under pressure; the core ranked result list cannot.
- **Autoscaling lag:** the query-result cache and existing shard-replica headroom absorb the first 1-3 minutes of a spike before autoscaling reacts — a trending-query spike is exactly the shape caching is built to absorb, since it concentrates on a small, repeated set of queries rather than spreading evenly.
- **Load-test target:** sustain 300,000 queries/sec for 10 minutes with p99 under 200ms, including a simulated single-shard slowdown — this confirms the partial-result fallback actually holds under real conditions, not just in isolation.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| A query's fan-out reaches a shard mid-segment-build | The new segment accumulates separately; the shard's "current segment set" pointer only swaps atomically once the new segment is fully built | Whichever segment set was current at the instant of the swap check — always complete and consistent, never half-written |
| Two crawler workers fetch the same URL concurrently | The crawl frontier's dedup store requires a conditional claim (URL marked "in-progress" via a conditional write) before a worker fetches | The second worker's claim fails and it moves on to the next frontier URL — no duplicate fetch, no duplicate indexing work |
| A popular query arrives while its cached result just expired and is being recomputed | A short-lived "recomputation in-flight" marker lets only the first arriving request trigger a fresh scatter-gather; concurrent requests for the same query key wait on that one recomputation | Later arrivals get the same freshly-computed result once it's ready, instead of every one of them independently stampeding all the relevant shards |
| A document is updated while a query's scatter-gather is already in flight against the old segment | The old segment stays valid and immutable until the swap; an in-flight query simply finishes against whichever segment set it started with | A fully consistent (if very slightly stale) result — never a mix of pre- and post-update state within one query |

## Scaling & Reliability

- **Horizontal scaling:** Query Coordinators and shard replicas scale independently, each on its own load axis — QPS for coordinators, term-space size and query load for shards.
- **Circuit breaker:** a shard that's timing out systematically (not just a one-off slow response) trips a breaker so the coordinator stops routing to it entirely for a cooldown window, instead of paying a timeout on every query that happens to need that shard's terms.
- **Graceful degradation:** a single replica outage falls back to another replica of the same shard (see Database Design's replication); if an entire shard is unavailable, the merge step still returns results from every other shard rather than failing the whole query — an incomplete-but-real result beats an error page.
- **Multi-region:** not built here, and worth naming as a real gap rather than a solved problem — see below.

## What you'd revisit as this grows

- **Multi-region serving**, so query latency doesn't include a cross-continent round trip — this design assumes a single region.
- **Personalized ranking** layered on top (a user's history, location) without breaking the "authority score is precomputed offline" separation — a genuinely different design axis from anything covered here.
- **Hot-term skew:** a small number of extremely common or suddenly-trending terms could overload specific shards even under hash partitioning — a mature system needs per-term load monitoring, with the option to give a hot term's postings extra replicas or split them further.
- **Real-time indexing** for a narrow slice of high-value, rapidly-changing sources (breaking news), rather than the hours-to-a-day batch cadence assumed for the rest of the web.
