# Module 02 — Low-Level Design

![Expanding-ring search: query the point's own cell plus its 8 neighbors first, widen only if too few candidates come back](diagrams/lld.svg)

**Nearby-query pseudocode:**
```
ProximityService.nearby(lat, lng, radius_km, limit):
    cell = geohash_encode(lat, lng, precision=default_for(radius_km))
    candidate_cells = [cell] + neighbors(cell)   # own cell + 8 surrounding cells

    candidates = []
    for c in candidate_cells:
        candidates += Store.getByGeohashPrefix(c)

    if len(candidates) < limit:                   # sparse area; own ring wasn't enough
        candidate_cells = expandRing(candidate_cells)
        candidates += Store.getByGeohashPrefix(candidate_cells - already_fetched)

    exact = [(e, haversine(lat, lng, e.lat, e.lng)) for e in candidates]
    exact = [pair for pair in exact if pair[1] <= radius_km]
    return sorted(exact, key=distance)[:limit]
```
Searching the point's own cell ALONE is a real bug, not just an optimization gap: a point sitting a few meters across a cell boundary from the query point can have a totally different geohash prefix despite being the closest match — the 8-neighbor search is what makes the geohash approach correct, not just fast.

**Distance ranking is a first pass, not the final answer.** For a rideshare-style query, a downstream ranking step (driver rating, ETA, availability) runs only over the small candidate set this query already narrowed down to — geohash + haversine filtering solves "which few thousand points are even plausible," not "which one is best," and keeps that expensive ranking step from ever running over the full dataset.
