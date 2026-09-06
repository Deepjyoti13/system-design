# Module 02 — Low-Level Design

![Expanding-ring search: query the point's own cell plus its 8 neighbors first, widen only if too few candidates come back](diagrams/lld.svg)

## Interfaces vs. implementations

- **`SpatialIndex`** *(interface)* → **`GeohashSpatialIndex`** — `encode(lat, lng, precision)`, `neighbors(cell)` (the 8 surrounding cells), `expandRing(cells)` (widen the search when a ring returns too few candidates). Every method here is pure computation, no storage — this is the strategy that turns a 2D point into the keys the stores are actually queried by.
- **`DynamicLocationStore`** *(interface)* → **`RedisGeoStore`** — `update(entityId, lat, lng)`, `getByGeohashPrefix(cell)`. Backed by an in-memory, TTL-expiring structure, matching Module 01's durability trade-off.
- **`StaticPlaceStore`** *(interface)* → **`SqlPoiStore`** — `getByGeohashPrefix(cell)`, plus category/attribute lookups for POI-specific filtering. Backed by a durable, indexed relational table.
- **`ProximityService`** — the orchestrator. Depends on `SpatialIndex` and both store interfaces, implements no storage or spatial math itself.

## Nearby-query pseudocode

```
ProximityService.nearby(lat, lng, radius_km, limit):
    cell = spatialIndex.encode(lat, lng, precision=default_for(radius_km))
    candidate_cells = [cell] + spatialIndex.neighbors(cell)   # own cell + 8 surrounding cells

    candidates = []
    for c in candidate_cells:
        candidates += dynamicStore.getByGeohashPrefix(c) + staticStore.getByGeohashPrefix(c)

    if len(candidates) < limit:                   # sparse area; own ring wasn't enough
        candidate_cells = spatialIndex.expandRing(candidate_cells)
        candidates += fetchFromBothStores(candidate_cells - already_fetched)

    exact = [(e, haversine(lat, lng, e.lat, e.lng)) for e in candidates]
    exact = [pair for pair in exact if pair[1] <= radius_km]
    return sorted(exact, key=distance)[:limit]
```
Searching the point's own cell ALONE is a real bug, not just an optimization gap: a point sitting a few meters across a cell boundary from the query point can have a totally different geohash prefix despite being the closest match — the 8-neighbor search is what makes the geohash approach correct, not just fast.

**Distance ranking is a first pass, not the final answer.** For a rideshare-style query, a downstream ranking step (driver rating, ETA, availability) runs only over the small candidate set this query already narrowed down to — geohash + haversine filtering solves "which few thousand points are even plausible," not "which one is best," and keeps that expensive ranking step from ever running over the full dataset.

## Concurrency at the code level

The nearby-query path needs no lock anywhere — `getByGeohashPrefix` is a plain read against whatever's currently stored, and Module 01's Concurrent-User Handling already covers why a few hundred milliseconds of staleness is invisible here, unlike a payment or a job claim.

The one place actual application-level care is needed is `RedisGeoStore.update(entityId, lat, lng)`, for exactly the out-of-order-ping race named in Module 01: the write has to be conditional on the incoming ping's own timestamp being newer than what's currently stored (`SET ... IF newer`, or the equivalent compare-and-set the underlying store supports), not a blind overwrite. Without that guard, a delayed, out-of-order ping arriving after a newer one would silently roll an entity's visible position backward — a correctness bug this system can't just shrug off as "eventually consistent," even though position freshness itself is loosely held.

## Design patterns you just used, named

- **Strategy pattern** — `SpatialIndex` is a strategy: `GeohashSpatialIndex` is one implementation; a quadtree- or R-tree-based implementation could replace it behind the same interface without `ProximityService` changing at all.
- **Repository pattern** — `DynamicLocationStore` and `StaticPlaceStore` hide storage behind method calls; the service never issues a raw Redis command or SQL query directly.
- **Pipeline (template method shape)** — `nearby()` is a fixed sequence — encode, fetch, filter, rank — that a later ranking strategy (driver rating, ETA) can be inserted into at the end without touching the earlier steps, the same "narrow first, then do the expensive thing" idea this guide applies elsewhere.

## Practice: extend it yourself

Before moving to Database Design, sketch (pseudocode is fine) how you'd add:

1. **Filtering by category while searching nearby** (`nearby(..., category="restaurant")`) — does the filter apply at the geohash-fetch step (which would need a composite index on `(category, geohash)`) or as a post-filter over the small candidate set the geohash search already narrowed down to? What changes if `category` is highly selective (a rare cuisine type) versus barely selective at all (any restaurant)?
2. **A stopping condition for ring expansion** — Module 01's sparse-area handling widens the search ring when too few candidates come back, but nothing in the pseudocode above says when to give up. What caps the expansion — a maximum radius, a maximum number of rings, a maximum candidates-fetched count — so a search anchored in the middle of the ocean doesn't expand forever?

Neither has one clean answer — the point is noticing that the interfaces already drawn (`SpatialIndex`, the two store interfaces) make it obvious which component *should* own each new piece of behavior, even before you've fully worked out what that behavior is.
