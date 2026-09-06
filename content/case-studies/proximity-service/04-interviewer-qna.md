# Module 04 — Interviewer Q&A

**What happens when two requests hit the same resource at the same instant?**
This system is the rare case where that's barely a race at all: a nearby-query reading a driver's position concurrently with a location-update ping just sees whichever value landed first — there's no shared mutable state that needs protecting, since one path only reads and the other only writes a single entity's own row.

**What happens when traffic spikes 10x for an hour?**
Geo-sharding contains the spike to whichever region is actually busy (a major event in one city) — shards covering unrelated regions see no additional load, which is a direct benefit of choosing a SPATIAL shard key instead of, say, hashing entity IDs uniformly across all shards.

**Why a geohash instead of separate indexes on `lat` and `lng` columns?**
A database index on `lat` alone (or `lng` alone) can narrow a search along ONE dimension but still leaves a large candidate set to filter along the other — a geohash folds both dimensions into a single sortable string, so one prefix range scan narrows both at once, which is exactly the leftmost-prefix reasoning [Database Indexing](../../database-design/database-indexing.md) covers generally.

**Why two separate stores instead of one table for both drivers and restaurants?**
They're the same shape of data with wildly different churn: optimizing one store for 250,000 writes/sec with no durability need (drivers) would be wasteful and unnecessary for data that changes once a month (restaurants), and optimizing for restaurant-style durability would make driver-location writes far too slow.

**How do you avoid missing a nearby point that falls just across a geohash cell boundary?**
Always search the query point's own cell plus its 8 immediate neighbors, never the query point's cell alone — two physically close points can land in different cells purely because of where the grid lines fall, and skipping the neighbor search would silently produce wrong (not just incomplete) results.

**What if the initial 9-cell search doesn't return enough candidates (a sparse rural area)?**
Expand the search to the next ring of surrounding cells (or drop to a coarser geohash precision covering a larger area per cell) and repeat, rather than returning fewer than the requested `limit` results whenever an area happens to be sparse.

**How does the dynamic store avoid growing unbounded with drivers who went offline without a clean disconnect?**
Every location entry carries a TTL tied to the expected ping interval — a driver who stops pinging simply ages out of the store on their own; there's no separate "mark offline" step or cleanup job required.

**How would you rank results by more than raw distance — driver rating, ETA, availability?**
Run distance filtering first specifically because it's cheap and narrows millions of points down to a small candidate set — the more expensive ranking factors then only need to be computed over that already-small set, not the full dataset.
