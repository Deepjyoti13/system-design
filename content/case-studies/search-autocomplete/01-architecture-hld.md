# Module 01 — Architecture & High-Level Design

![Sync read path (client to trie-serving replicas to personalization) versus the async frequency pipeline (search log to batch aggregator to snapshot store), with the atomic-swap loop closing it](diagrams/hld.svg)

## Monolith vs. microservices

The one seam worth pulling out deliberately is the **trie-serving tier itself**, not because of team ownership boundaries but because of a hardware fact: this tier's entire job is holding tens of gigabytes of a single data structure in process memory and answering reads against it, which is a completely different scaling axis (memory-optimized instances, no disk I/O on the read path) than the stateless API/routing layer in front of it or the batch pipeline behind it. Folding trie-serving into the same process as request routing would mean every API instance needs the full snapshot loaded, multiplying memory cost by however many stateless API replicas you run for throughput reasons alone — reasons that have nothing to do with how much vocabulary you're indexing.

If the vocabulary were small enough to fit trivially in any instance's memory (a product-catalog autocomplete with 50K SKUs, say, not 50M open-ended search phrases), this split buys nothing — a single process holding the whole trie behind a normal stateless API would be the right call, and a dedicated trie-serving tier would be solving a scale problem that doesn't exist yet.

## Building Blocks

| Block | Role |
|---|---|
| **Autocomplete API** (stateless) | Accepts `GET /autocomplete`, forwards to the Prefix Router; the only component a client talks to directly |
| **Prefix Router** (stateless) | Maps a query's leading character(s) to the one shard that can answer it — no fan-out, ever |
| **Trie-Serving Replicas** (in-memory, sharded) | Hold a read-only, immutable trie snapshot per shard; every branching node caches its own precomputed top-K |
| **Personalization Blend** (stateless, optional) | Blends a user's recent-search signal on top of the base ranking at query time; the first thing skipped under load |
| **Search Log Queue + Event Log** | Durable record of completed/selected searches, decoupled from the read path entirely |
| **Frequency Aggregator** (batch job) | Periodically recomputes term frequency from the log and rebuilds each node's cached top-K |
| **Snapshot Store** (blob storage, versioned) | Holds each complete, immutable trie generation; replicas pull the latest and swap a pointer |

## Per-path walkthrough

**Read path (sync, every keystroke)** — `Client → Autocomplete API → Prefix Router → Trie-Serving Replica (walk to the prefix's node, O(prefix length)) → Personalization Blend (optional) → Client`. Nothing on this path writes anything, queries a database, or blocks on another network call beyond the one hop to the owning shard — this is the entire latency budget from Capacity Estimation spent on memory access, not I/O.

**Frequency path (async, every few minutes)** — `Client (search-selected event) → Search Log Queue → Search Event Log → Frequency Aggregator (batch, reads a closed time window) → TrieBuilder (constructs the next generation off to the side) → Snapshot Store (publish, versioned)`. Entirely decoupled from the read path — a slow or backed-up aggregator run makes rankings a few minutes staler, never makes a suggestion request wait.

**Snapshot-adoption path (async)** — `Snapshot Store → Trie-Serving Replicas (poll for latest version, pull, atomic pointer swap)`. This is the seam where "new data" crosses into "what the read path sees," and it happens without ever mutating a structure a reader might be mid-traversal through (see Concurrent-User Handling, and the full mechanism in [Module 02 — LLD](02-lld.md)).

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Serving structure | In-memory trie, cached top-K per node | Relational table + `LIKE 'prefix%'` query | A B-tree serves exact-match/range lookups, not a re-run-every-keystroke prefix scan at ~46K/sec — see Overview |
| Sharding key | Prefix range (first character(s) → shard) | Hash of the full term | A hash spread scatters every term starting with "sal" across every shard, forcing a fan-out on every query; prefix-range sharding means one query touches exactly one shard, because the shard boundary matches the access pattern itself |
| Freshness mechanism | Async batch recompute + snapshot swap | Update the live trie's counters synchronously per search | Synchronous updates would turn search *traffic* into *write load* on the exact structure serving read traffic — the opposite of what an in-memory cache is for |
| Snapshot adoption | Build a whole new immutable generation, then one atomic pointer swap | Mutate node fields in place as new data arrives | An in-place mutation risks a reader observing a half-updated node mid-traversal, or needs a lock on the hottest path in the whole system; an atomic swap makes "old" and "new" the only two possible answers, never "partial" |
| Personalization | Global ranked trie + a thin per-user blend at query time | A separate full trie per user | A full trie per user doesn't scale storage-wise and mostly isn't needed — the global ranking already covers the overwhelming majority of a query's completions |

## Load Handling

- **Peak-vs-average tolerance:** the 5x diurnal spike from Capacity Estimation (9,260/sec avg → 46,300/sec peak) lands entirely on the Trie-Serving Replicas and API tiers, both of which are read-only against an already-loaded structure — an ordinary horizontal-scaling problem, add replicas per shard behind the router.
- **Where backpressure kicks in first:** not the read path — a cheap, lock-free, in-memory lookup doesn't back up under load the way a database connection pool does. The real backpressure point is the **Search Log Queue** during a viral event, when a burst of never-before-seen terms floods the async pipeline. That's invisible to users; it only delays how soon a brand-new term appears in results.
- **What gets shed under overload:** **Personalization Blend first, always** — it's an enrichment layer on top of an already-correct base answer, so skipping it under pressure degrades personalization, never correctness or availability. The base cached top-K lookup itself is never shed; if it can't be served, the request fails outright rather than silently returning a wrong or empty list dressed up as a real answer.
- **Autoscaling lag:** a freshly started trie-serving replica must pull and load the full multi-gigabyte snapshot before it can serve — real ramp-up time a purely reactive autoscaler can't absorb fast enough for a demand spike that's minutes away. Scale ahead of predictable peaks (a known regional evening curve, a scheduled sale event) rather than waiting for a threshold breach.
- **Load-test target:** sustain 50,000 requests/sec against a fully-loaded shard set with p99 latency under 100ms, and zero read-path errors while a live snapshot swap runs concurrently underneath the traffic.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| A read request arrives while a trie-serving replica is mid-swap to a new snapshot generation | The swap is a single atomic pointer/reference reassignment; a reader that already started reads whichever generation it captured at the start of its walk (see [LLD](02-lld.md)) | Nothing "lost" — the request simply resolves against the old or new generation, in full, never a mix of the two |
| Thousands of concurrent requests hit the exact same trending node during a viral spike | The cached top-K list is read-only once published — no lock is needed because nothing is being written on the read path | All readers succeed identically and simultaneously; this is the entire point of serving from an immutable structure |
| The Frequency Aggregator is mid-rebuild while new search-selected events for a brand-new term keep arriving | The aggregator reads a closed, checkpointed time window (`events up to time T`), not an unbounded live stream — anything past the cutoff simply rolls into the *next* run | A never-before-seen term doesn't appear in suggestions until the following batch cycle — an explicit, accepted staleness window, not a bug |
| A single user types quickly, firing overlapping requests for "s", "sa", "sal" whose responses can arrive out of order | This is a client-side concern the backend doesn't and shouldn't solve — flag it explicitly rather than silently ignoring it | The client is expected to cancel or ignore a response for a prefix it's already moved past; the backend has no way to know a response is stale to a client that's already typed further |

## Scaling & Reliability

- **Horizontal scaling:** Trie-Serving Replicas scale per shard by adding read replicas behind the Prefix Router; the router itself holds only a small, rarely-changing prefix→shard map and needs no coordination to scale.
- **Circuit breaker:** if every replica behind a shard is failing health checks, requests to that shard trip a breaker (cross-ref [Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md)) and fall back immediately rather than piling up latency against a shard that's already down.
- **Retries:** a cold-start replica's snapshot pull from blob storage is idempotent and retried with backoff; a client's own suggestion request is safe to retry directly, since the lookup is a pure read.
- **Fallback, not a dead-letter queue:** if a shard is fully unavailable, return a small, static, globally-replicated "top queries" list rather than an error — a generic fallback beats no suggestions at all, and it costs nothing to keep warm everywhere.
- **Graceful degradation:** if the Search Log Queue or Frequency Aggregator is down, the read path is entirely unaffected — replicas keep serving the last successfully-published snapshot, growing slowly staler, since freshness is explicitly decoupled from availability by design (see Trade-offs).
- **Multi-region:** replicate the (comparatively small, infrequently-updated) trie snapshot to every region's blob store; each region runs its own local Trie-Serving Replicas loading that snapshot, so reads never cross a region boundary — only the periodic snapshot publish does.

## What you'd revisit as this grows

- **Dynamic re-sharding.** Static prefix ranges assume roughly even load per range; a single viral prefix can make one shard disproportionately hot, which this design doesn't rebalance automatically.
- **Faster trend reaction.** The batch cycle (minutes) is a deliberate trade for keeping the read path pure-read; a "trending now" boost that reacts in near-real-time would need a second, much smaller and faster signal blended in without turning the whole pipeline synchronous (see [LLD](02-lld.md) practice exercises).
- **Typo and fuzzy-match tolerance.** A pure trie only helps with exact-prefix matches; this design deliberately layers that on as a separate pass rather than baking it into the trie itself.
- **Regional write aggregation.** Search-selected events are assumed to funnel into one pipeline; a truly global product would need regional log aggregation before centralizing for the Frequency Aggregator.
