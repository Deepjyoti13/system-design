# Module 04 — Interviewer Q&A

**1. What happens when two requests hit the same resource at the same instant?**
This system is the rare case where that's barely a race at all: a nearby-query reading a driver's position concurrently with a location-update ping just sees whichever value landed first — there's no shared mutable state that needs protecting, since one path only reads and the other only writes a single entity's own row.

**2. What happens when traffic spikes 10x for an hour?**
Geo-sharding contains the spike to whichever region is actually busy (a major event in one city) — shards covering unrelated regions see no additional load, which is a direct benefit of choosing a SPATIAL shard key instead of, say, hashing entity IDs uniformly across all shards.

**3. Why a geohash instead of separate indexes on `lat` and `lng` columns?**
A database index on `lat` alone (or `lng` alone) can narrow a search along ONE dimension but still leaves a large candidate set to filter along the other — a geohash folds both dimensions into a single sortable string, so one prefix range scan narrows both at once, which is exactly the leftmost-prefix reasoning [Database Indexing](../../database-design/database-indexing.md) covers generally.

**4. Why two separate stores instead of one table for both drivers and restaurants?**
They're the same shape of data with wildly different churn: optimizing one store for 250,000 writes/sec with no durability need (drivers) would be wasteful and unnecessary for data that changes once a month (restaurants), and optimizing for restaurant-style durability would make driver-location writes far too slow.

**5. How do you avoid missing a nearby point that falls just across a geohash cell boundary?**
Always search the query point's own cell plus its 8 immediate neighbors, never the query point's cell alone — two physically close points can land in different cells purely because of where the grid lines fall, and skipping the neighbor search would silently produce wrong (not just incomplete) results.

**6. What if the initial 9-cell search doesn't return enough candidates (a sparse rural area)?**
Expand the search to the next ring of surrounding cells (or drop to a coarser geohash precision covering a larger area per cell) and repeat, rather than returning fewer than the requested `limit` results whenever an area happens to be sparse.

**7. How does the dynamic store avoid growing unbounded with drivers who went offline without a clean disconnect?**
Every location entry carries a TTL tied to the expected ping interval — a driver who stops pinging simply ages out of the store on their own; there's no separate "mark offline" step or cleanup job required.

**8. A location ping arrives out of order — a network retry delivers an OLDER position after a newer one already landed. What happens?**
The write is conditional on the incoming ping's own timestamp being newer than what's currently stored, not a blind overwrite — the out-of-order ping is silently discarded, and the entity's visible position never rolls backward. This is the one place this system needs real application-level care, precisely because everywhere else it can skip correctness machinery that a payment or a job-claim system can't.

**9. Would this design work the same way for a slowly-moving entity (a delivery bike) versus a rarely-moving one (a parked, shareable scooter)?**
Yes — churn rate is a spectrum, not a binary. The ping interval and TTL are tunable per entity type without changing either store's shape; an entity that updates its position as rarely as a restaurant's listing could even live in the static store instead, since "how often does this move" is the only variable that determines which store an entity belongs in, not what kind of entity it is.

**10. How would you extend this for a search radius that spans a geo-shard boundary?**
Query the neighboring shard(s) for the cells near the boundary too, and merge and re-sort the combined candidates the same way a single-shard search already sorts its own — the same fan-out-and-merge cost this guide names for any cross-shard query, but rare here since it only affects searches anchored close to a shard's edge, not typical queries.
