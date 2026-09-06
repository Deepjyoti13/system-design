# Module 01 — Architecture & High-Level Design

![Geospatial 9-cell matching, and the full ingestion-to-fare architecture](diagrams/hld.svg)

## Monolith vs. microservices

Location ingestion and matching are pulled out as their own services, never folded into a general Trips or Payments monolith, because they have a fundamentally different operational profile from everything else in this system. The location stream (1.25M writes/sec, sustained) needs an in-memory geospatial index that lives entirely in RAM and is rebuilt from a live stream, not a durable database with the usual backup/restore/replication story — running that alongside trip records and payment ledgers in one service would mean either starving the location path of resources during a trip-history reporting query, or over-provisioning the entire monolith to the ingestion tier's throughput needs. Matching has its own reason to be separate again: it has a hard sub-3-second latency budget that a general-purpose service handling fare disputes or trip history queries has no business sharing a deploy cadence or a resource pool with.

The seam sits exactly at "what needs the geospatial index in memory, right now" versus "what's a normal ACID-backed CRUD service." If your platform is small enough that a single database's spatial extension handles the whole matching workload comfortably, this split isn't earning its operational cost yet — the decision should trace back to the 1.25M/sec number from Capacity Estimation, not to a general instinct that ride-sharing "obviously" needs microservices.

## Building blocks

| Block | Role |
|---|---|
| **Location-Ingestion Service** (stateless) | Holds a persistent connection per driver (same stateful-connection shape as [Long Polling, WebSockets & SSE](../../scalability-resilience/long-polling-websockets-sse.md)) and forwards each `location.update` into the geospatial index — never touches the matching decision |
| **Geospatial Index** — in-memory, geohash-bucketed | Buckets drivers by geohash cell; "find nearby available drivers" becomes a 9-cell lookup against a few hundred candidates, never a scan of 5M |
| **Matching Service** (stateless) | Ranks the candidate set by real distance/ETA and atomically claims the nearest available driver |
| **Trip Service** | Owns the trip state machine (`requested → matched → in_progress → completed`) and is the only writer of the trip's final fare record |
| **Pricing/Fare Service** | Computes a fare estimate at request time and the final fare at trip end; kept separate because pricing logic changes far more often than the trip state machine does |

## Per-path walkthrough

**Ride-request-to-match path (write)** — `Rider → LB → Trip Service (create trip, status=requested) → Matching Service (query Geospatial Index for 9-cell candidates) → Matching Service (atomic claim on nearest candidate) → Trip Service (status=matched) → Rider + Driver notified`. The claim is the one step in this path that has to be atomic against concurrent claims — everything before and after it is a straightforward read or a single-row write.

**Driver-location-update path (the dominant write volume)** — `Driver app → Location-Ingestion Service → Geospatial Index (update driver's cell membership + position)`. This path never touches a durable database and never touches the Matching Service directly; it exists purely to keep the index fresh for whenever a nearby match query happens to read it.

**Fare-calculation path (async, at trip end)** — `Trip Service (status=in_progress → completed) → Pricing/Fare Service (compute final fare from recorded distance/time + demand multiplier) → Trip Service (write final fare)`. Decoupled from the trip's own completion — see Scaling & Reliability below for what happens if this service is down.

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Spatial index structure | Geohash grid (fixed-precision cells) | Quadtree / R-tree | A geohash cell is a plain string prefix — bucketing and neighbor lookup are O(1) hash operations; a quadtree needs tree traversal for the same neighbor query, more code and more latency for a lookup that runs on every single ride request |
| Driver location delivery | Push (driver streams updates over a persistent connection) | Pull (server polls each driver's last-known position on demand) | At 5M drivers, polling on demand means either polling constantly (same load as push, more complex) or accepting stale reads on-demand; push keeps the index continuously warm for the one read path that actually needs freshness |
| Matching mechanism | Synchronous nearest-candidate claim | Auction / bidding (drivers see the request and choose to accept) | The 3-second match latency target rules out waiting for driver responses; a direct claim against the geospatial index's ranked candidates is the only mechanism that fits the budget |
| Location data store | In-memory (Redis), no durability | Durable relational store with a spatial index (PostGIS-style) | A location update is stale in 4 seconds regardless of how it's stored — paying for durability and replication on data with a 4-second useful life is pure waste; see Database Design |
| Candidate ranking, first pass | Straight-line distance | Real routing-engine ETA for every candidate | Straight-line distance is nearly free and good enough to narrow 5M drivers to a handful; only the final top few candidates justify the expense of a real routing call |

## Load Handling

- **Peak-vs-average tolerance:** the dominant load is the 1.25M/sec location stream, not ride requests (120/sec peak is noise by comparison) — ingestion scales horizontally by sharding drivers across ingestion instances by driver ID, so absorbing more drivers is adding instances, not a re-architecture.
- **Where backpressure kicks in first:** a surge event (a stadium letting out, a sudden storm spiking ride demand in one neighborhood) doesn't raise the location stream's volume at all — it concentrates ride *requests* into a handful of geohash cells, which is a contention problem on those specific cells' candidate pools, not a throughput problem system-wide.
- **What gets shed under overload, in deliberate order:** first, update frequency for idle/parked drivers (4s → 15s costs nothing real — a parked car isn't moving); next, active-but-far-from-any-pending-request drivers; only as an absolute last resort, updates for drivers *currently on a trip*, since that's the one feed a rider is actively watching in real time. Ride-matching itself is never shed — a rider who can't get a match sees an honest "no drivers nearby," never a silently dropped request.
- **Autoscaling lag:** the ingestion tier's per-shard capacity is provisioned with headroom for a metro-wide reconnect storm (every driver's app reconnecting within seconds after a regional network blip) specifically because autoscaling reacts on a 1-3 minute horizon and a reconnect storm arrives in under 10 seconds — existing headroom, not new instances, absorbs the first minute.
- **Load-test target:** sustain 1.5x peak ingestion (≈1.9M writes/sec) with p99 geospatial-index staleness still under 10 seconds, and separately, sustain 10x ride-request peak (~1,200/sec) concentrated into a single dense downtown geohash cell without the claim-conflict rate on that cell degrading match latency past the 3-second target.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| Two ride requests both compute the same driver as nearest-available and both attempt to claim at the same instant | Atomic conditional update (`UPDATE drivers SET status='claimed' WHERE id=? AND status='available'`) — only one claim's write affects a row | Zero rows affected; immediately re-matched against the next-nearest candidate from the same cell lookup, never bounced back to the rider as a bare failure |
| A driver's location update arrives while that driver is mid-claim | The claim and the location write touch different fields and don't structurally conflict, but the matching service treats the driver's position as a snapshot taken at claim time | The rider sees the position the driver was actually at when claimed, not a slightly-later position that could show the driver already moving away |
| A driver goes offline (app killed, connectivity lost) in the instant between being selected as a candidate and the claim executing | The claim's own conditional update is the safety net — an offline driver's status is set to unavailable by the ingestion tier's connection-drop handler, so the claim's `WHERE status='available'` simply fails for that driver, same as any other lost race | Immediately re-matched to the next candidate, exactly like the first race — from the matching service's point of view, "driver went offline" and "driver got claimed by someone else" are the same kind of failed claim |
| Two riders' requests land in the same geohash cell with only one available driver between them | Resolved by whichever request's claim reaches the atomic update first — not a fairness mechanism, a race the system doesn't try to arbitrate beyond first-committed-wins | The losing rider's request re-queries the same 9-cell neighborhood; if genuinely no other driver is available, they see an honest "no drivers nearby" rather than an indefinite wait |

## Scaling & Reliability

- **Horizontal scaling:** both the Location-Ingestion Service and Matching Service are stateless per request and scale behind their respective tiers by load; the Geospatial Index itself scales by geographic sharding (see Database Design), not by adding more application instances.
- **Circuit breaker:** the Matching Service's call into the routing engine (for real ETA on the final candidates) is wrapped in a circuit breaker (cross-ref [Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md)) — if the routing engine is slow or down, matching falls back to straight-line-distance ranking alone rather than blocking every match on a dependency that isn't on the critical path for correctness.
- **Retries:** a failed claim is not retried against the *same* driver — it's an immediate re-match against the next candidate, since the failure means someone else has already claimed that driver, not that the request itself failed transiently.
- **Graceful degradation:** if the Geospatial Index (Redis) is degraded in one shard, matching in that metro area degrades to a wider-radius or slower fallback lookup rather than failing outright — a slow match beats no match. If the Location-Ingestion Service for a shard is fully down, the affected drivers' last-known positions simply age past the 10-second staleness target and matching temporarily prefers other shards' drivers, self-correcting once ingestion recovers.
- **Multi-region:** run per-city (or per-metro-cluster) rather than one global deployment — a ride in one city has zero need to ever consider a driver in another, so region boundaries can follow the natural geographic partition instead of needing cross-region conflict resolution the way a globally-shared dataset would.

## What you'd revisit as this grows

- **Cross-city ride requests** (a rider requesting a ride that starts in one metro's shard and needs a driver from an adjacent one at a boundary) aren't handled by this design's clean per-shard partitioning — worth naming as a real edge case rather than assuming metro boundaries are always where a rider stands.
- **Surge pricing feedback into matching itself** (routing a request to a slightly farther driver because the nearest one is more valuable serving a longer trip) is a real production concern this design deliberately keeps out of the Matching Service's core logic, to keep the claim path simple and fast.
- **Driver-side preferences** (a driver who only wants trips above a certain fare, or heading a certain direction) would need the candidate ranking to become two-sided instead of purely rider-nearest-driver, a meaningfully harder matching problem.
- **Geohash precision tuning per density** — a fixed cell size undersells dense downtown cores (too many drivers per cell) and oversells sparse suburbs (too few); a mature system varies precision by local driver density rather than using one global setting.
