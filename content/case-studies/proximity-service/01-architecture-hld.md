# Module 01 — Architecture & High-Level Design

![A geohash turns a 2D radius search into a small set of prefix lookups, split across a churn-optimized dynamic store and a durable static store](diagrams/hld.svg)

## Monolith vs. microservices

Proximity search earns its own service boundary for a reason that's structural, not organizational: it needs a data structure and a sharding scheme (geohash-prefix, spatially aware) that nothing else in a typical platform needs. A general-purpose Orders or Users service has no reason to shard by geographic proximity — folding "find nearby entities" into either would force an unrelated data model onto tables that have nothing to do with location, or would leave proximity search bolted onto a store that isn't shaped for it (a plain relational table sharded by `user_id` answers "this user's orders" fine and "what's within 2km of this point" terribly).

The second reason the seam holds: this system's two stores split by **churn rate**, not by feature area — the dynamic location store and static POI store have opposite operational profiles (one is all writes with no durability need, the other is read-heavy and durable) and would fight each other for tuning priorities if forced to share infrastructure with a general application database. Isolating both behind one Proximity Service means the rest of the platform asks one question — "what's near this point?" — without needing to know that the answer is assembled from two entirely different systems underneath. If your platform only ever needs a handful of fixed locations (a company's own office addresses, say), this split is overkill — a plain indexed table answers that fine, and standing up a geohash-sharded architecture for a few dozen rows is solving a scale problem you don't have.

## Building blocks

**Two stores, split by churn rate, not by entity type.** A driver's location and a restaurant's location are the same KIND of data (a geohash-indexed point), but they have wildly different write patterns, so they live in different systems:
- **Dynamic location store** (an in-memory geo-indexed structure, e.g. Redis `GEOADD`/`GEOSEARCH`) for frequently-moving entities — optimized for extremely high write throughput, with no durability requirement, since a stale or lost entry is corrected by the next location ping seconds later.
- **Static POI store** (a relational table with a geohash-prefix index, cross-ref [Database Indexing](../../database-design/database-indexing.md)) for rarely-changing points — optimized for durability and heavy read volume, not write throughput.

**Geo-sharding** — both stores are partitioned by geohash prefix (cross-ref [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md), [Consistent Hashing](../../hld-building-blocks/consistent-hashing.md)): the same spatial-locality property that makes geohashes useful for querying also makes them a natural shard key — a "nearby" query for a point in one city resolves against one or two shards, never the whole planet's worth of data.

**Caching the static store's hot regions** — city-center POI density means a small fraction of geohash cells serve a disproportionate share of queries; a cache-aside layer (cross-ref [Caching Strategies](../../hld-building-blocks/caching-strategies.md)) in front of the static store absorbs this skew, since restaurant listings changing once a day tolerate a cache far more comfortably than a driver's position would.

## Per-path walkthrough

**Search path (read)** — `Client → LB → Proximity Service → SpatialIndex (encode query point, compute own cell + 8 neighbors) → Dynamic Store AND/OR Static Store (fetch candidates by geohash prefix, in parallel) → Proximity Service (haversine-filter to exact radius, sort by distance) → Client`. Both stores are queried the same way, by the same geohash-prefix mechanism — the split between them is invisible to the caller, who just asked "what's nearby."

**Location-update path (write, dynamic entities only)** — `Client (driver's device) → LB → Proximity Service → Dynamic Store (upsert entity's geohash + lat/lng, TTL refreshed)`. No transaction, no durability write, no downstream fan-out — the entire point of this path is that it's as cheap as a write can be, since it runs at a much higher frequency than any other path in this system (see Capacity Estimation).

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Spatial index structure | Geohash (prefix string encoding) | Quadtree or R-tree | A geohash folds 2D proximity into an ordinary sortable string — reusable by a plain B-tree index or an in-memory sorted set with no custom tree-balancing code; quadtrees/R-trees model the search circle more tightly but need bespoke data structures that don't fall out of infrastructure you already have |
| Data storage | Two stores, split by churn rate | One unified store for all entities | A store tuned for 250,000 writes/sec with no durability need (drivers) is wasted cost for data that changes once a month (restaurants), and tuning for restaurant-style durability would make driver-location writes far too slow |
| Geohash precision | Computed per-query from the requested radius | One fixed precision for every query | A fixed precision either wastes cells fanning out for a large-radius search or is too coarse to distinguish points for a small-radius one; deriving precision from the radius keeps the neighbor-fetch count roughly constant regardless of query shape |
| Dynamic-entity durability | In-memory only, TTL-based expiry | Durable database with an explicit "mark offline" cleanup job | Paying for durability on data that's stale within seconds anyway is pure waste; a TTL lets an offline entity age out on its own, with no cleanup job to build, run, or forget to run |
| Cell-search width | Always fetch the query point's own cell plus its 8 neighbors | Fetch only the query point's own cell | Skipping the neighbor cells is a correctness bug, not just a missed optimization — a point a few meters across a cell boundary can be the closest match and would be silently excluded |

## Load Handling

- **Peak-vs-average tolerance:** the dominant load risk is a regional write burst — a city's evening rush hour driving up location-ping volume in exactly the shards covering that city, while unrelated regions' shards see no additional load at all, a direct benefit of a spatial shard key over a uniformly-hashed one.
- **Where backpressure kicks in first, on the write side:** a location ping that hasn't moved meaningfully (within a few meters of the last recorded position) is dropped before it ever reaches the store — most write amplification in a system like this comes from stationary or slow-moving entities re-reporting a position that hasn't changed, so this filter alone absorbs a large fraction of ordinary load growth for free.
- **The harder case is a read hotspot, not a write one:** a single popular event (a stadium letting out, a large gathering) concentrates a burst of *searches* on a handful of geohash cells — unlike a write burst, this can't be spread across more shards, because the queries are all legitimately about the same small patch of the map. What absorbs it: the cache-aside layer in front of the static store, and, for the dynamic store, simply serving from whichever shard already owns that region at higher concurrency rather than trying to redistribute the hot region's data mid-event.
- **What gets shed under overload:** the downstream ranking pass (driver rating, ETA, availability — see Module 02) is the first thing to skip; the service still returns raw distance-sorted candidates from the geohash fetch, which answers the core question even without the enrichment. The geohash lookup itself — the query's actual value — is never shed.
- **Autoscaling lag:** the dynamic store's read/query tier scales on a 1-3 minute horizon; the stationary-ping filter and the cache-aside layer's existing hit rate provide the headroom during that gap, not new capacity arriving instantly.
- **Load-test target:** sustain 250,000 location-update writes/sec against one region's shard, plus 10,000 nearby-queries/sec against the same hot region, with p99 query latency under 100ms and zero location-store write failures, for 10 minutes.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| A driver's location updates mid-search (a rider's nearby-query is mid-flight when a new ping lands) | No locking at all — the dynamic store read simply returns whatever position is current at the instant it's read | A position that's, at worst, a few hundred milliseconds stale; a subsequent search sees the corrected position, with no error or retry needed |
| A nearby point sits just across a geohash cell boundary from the query point | Not actually a race between users — a structural risk in the algorithm itself, closed by always fetching the query point's own cell plus its 8 neighbors (never the query's own cell alone) | Nothing — the point is still returned, because the 9-cell fetch is defined specifically to catch it, not because any lock or retry intervened |
| Out-of-order location pings for the same entity (network reordering, a mobile client retry delivering an older ping after a newer one already landed) | Each ping carries its own client-side timestamp; the write is conditional — only applied if the ping's timestamp is newer than the currently stored one | The out-of-order (older) ping's write is silently discarded; the entity's visible position is never rolled backward to a stale value |

## Scaling & Reliability

- **Horizontal scaling:** both stores scale by adding geo-shards — since shard boundaries follow geohash prefixes, growth means splitting a dense region's cells across more nodes, not a global rebalance.
- **Circuit breaker / retries:** the static store's reads are wrapped in a circuit breaker (cross-ref [Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md)) so a struggling shard fails fast instead of piling up connections that starve requests to healthy shards. Dynamic-store writes deliberately skip retry logic entirely — a failed ping is superseded by the next one arriving seconds later, so retrying it is wasted work, not a correctness requirement.
- **Graceful degradation:** the two stores fail independently, which is a real reliability advantage of splitting them. If a region's dynamic-store shard is down, driver-position searches in that region fail explicitly, but static POI results (restaurants, landmarks) from the unaffected static-store shard keep working normally — a single outage never takes down both halves of "nearby" at once.
- **Multi-region:** closer to free here than in most systems in this guide, precisely because of data locality — a "nearby" query anchored in Tokyo never has any reason to touch a shard serving the US, so a region-per-geography deployment is a natural fit rather than a hard problem layered on afterward.

## What you'd revisit as this grows

- **Hot-cell handling for a genuinely massive real-time event** (a stadium emptying out, a citywide event) — the same problem this guide's [Counting a Billion Likes](../../../like-counting-at-scale/00-overview.md) case study names for a viral post: a fixed geo-shard boundary can't be resharded instantly mid-event, and a real system would need to detect and temporarily absorb a hot region rather than assume geo-sharding alone smooths every spike.
- **Cross-shard search near a shard boundary** — a search radius anchored close to the edge of one geo-shard's region can miss candidates that live just across the boundary in a neighboring shard; this design doesn't yet fan out to adjacent shards for that edge case.
- **Category and attribute filtering combined with the spatial index at the storage layer**, rather than as a post-filter over whatever the geohash fetch already returned — sketched as a practice exercise in Module 02, but genuinely unresolved here.
- **Full position history for a moving entity** is explicitly out of scope — the dynamic store only ever holds an entity's *current* position, by design, and a durable history pipeline (for ETA-model training or analytics) would be a genuinely separate system layered on top, not an extension of this one.
