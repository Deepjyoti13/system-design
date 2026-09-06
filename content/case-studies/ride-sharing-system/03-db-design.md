# Module 03 — Database Design & Scaling

![Relational tables plus the non-relational driver_locations hot path](diagrams/er.svg)

## From entities to schema

- **`drivers`** — id, current status, vehicle info. Small, low write-rate, a normal relational table.
- **`riders`** — id, profile, payment method reference.
- **`trips`** — id, rider_id, driver_id, state, pickup/destination, fare. The durable, ACID-sensitive record of what actually happened.
- **`driver_locations`** — architecturally *not* a normal table. At 1.25M writes/sec of data that's stale in 4 seconds anyway, this belongs in an in-memory store (Redis, holding each driver's latest position plus geohash-cell membership) rather than a durable relational table — the same reasoning [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md) applies to "don't default to the primary DB for a workload with fundamentally different characteristics." Nothing about ride history needs the 4-seconds-ago position once the trip is over.

## Indexes

- `drivers(status)` — the claim's own `WHERE status='available'` and the candidate-filtering step both scan by status; a driver's status changes frequently but the table itself is small (millions, not billions, of rows), so a plain B-tree index on status stays cheap to maintain.
- `trips(rider_id, created_at)` — a rider's ride-history pagination is the most common trip-table read, always scoped to one rider and ordered by recency.
- `trips(driver_id, created_at)` — a driver's earnings view is the same access pattern from the other side of the relationship; a second index is cheaper than forcing that query to scan `rider_id`-ordered data.
- `driver_locations` — no relational index at all. The geohash bucket structure in Redis (a sorted set or hash keyed by cell) **is** the index; there's no separate index-maintenance step because the bucket assignment and the storage are the same write.

## Consistency

- **`trips` and the final fare:** strongly consistent, matching every other ACID-sensitive record in this guide — a trip's state transitions and its fare are read by both the rider and the driver, and by billing, so an inconsistent read here is a real dispute, not a cosmetic glitch.
- **`driver_locations`:** deliberately not strongly consistent, and this is a design choice stated in Module 00, not an accident — a position that's up to 4 seconds stale is an explicit, acceptable trade for keeping the write path in memory and off the durable-storage critical path entirely. There is no "correct" version of a driver's position to be consistent *with*; the position itself is only ever an approximation of "where they probably are right now."
- **`drivers.status`:** strongly consistent for the specific claim operation (the conditional update in Module 02), but eventually consistent from the geospatial index's point of view — the index's candidate list can include a driver who was claimed a moment ago and hasn't been evicted from the bucket yet, which is exactly why the claim itself, not index membership, is the actual source of truth for "is this driver still gettable."

## Scaling the schema

**Sharding:** `driver_locations` is sharded geographically — a metro area's drivers stay in one shard, matching the geospatial index's own partitioning, so a nearby-driver lookup never has to fan out across shards (see [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md)). `trips` is sharded by `rider_id` or `trip_id` instead, since trip history is queried per-user, not per-geography, and geographic sharding would scatter one rider's trip history across every metro they've ever ridden in.

**Read replicas vs. sharding, again:** replicas would help `trips` reporting/history queries scale their read throughput; they do nothing for `driver_locations`, whose bottleneck is write volume and freshness, not read fan-out — the two tables' scaling stories are genuinely different because their access patterns are, which is the point of naming the distinction rather than reaching for one lever by default.

## Connecting it back

Look at all three modules together: Module 00's observation that the location stream "dwarfs everything else numerically" is why `driver_locations` is the one entity in this schema that isn't a normal relational table at all; that same observation is why its consistency model in this module is deliberately weaker than `trips`'; and the geographic sharding here is what makes the 9-cell lookup in Module 01 and Module 02 a single-shard operation instead of a cross-shard fan-out. Nothing about this schema's asymmetry — one hot, ephemeral, in-memory table sitting next to normal ACID tables — is arbitrary; it traces directly back to the one number that dominates this case study's requirements.
