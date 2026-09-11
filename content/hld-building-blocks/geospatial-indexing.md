# Geospatial Indexing

![The same neighbourhood under four schemes: a uniform grid, a geohash prefix tree, a density-adaptive quadtree, and S2 cells along a Hilbert curve](diagrams/geospatial-indexing.svg)

## What problem this solves

"Find everything within 5 km of me" looks like an ordinary range query and isn't. The reason is one line:

```sql
SELECT id FROM places
 WHERE lat BETWEEN 37.75 AND 37.80
   AND lng BETWEEN -122.45 AND -122.40;
```

**A B-tree index can only accelerate one dimension.** With an index on `(lat, lng)`, the database narrows to the latitude band — a strip wrapping the entire planet — and then scans every row in it checking longitude. Two separate indexes are worse: the planner picks one, or intersects two large result sets. Either way you've turned a proximity query into a scan of a global band.

So the whole field of geospatial indexing exists to do one thing: **collapse two dimensions into one**, so that a single ordinary index becomes useful. Every scheme below is a different way of ordering 2D space onto a line such that points close on the line are close on the ground.

## Option 1 — A uniform grid

Divide the world into fixed-size cells; index by cell id.

```
cell_id = floor(lat / 0.01) * 100000 + floor(lng / 0.01)
```

A query reads its own cell plus the neighbours the radius touches. Simple, O(1) to compute, trivial to update — a moving object just changes cell id.

**Why it's rarely enough:** population density varies by many orders of magnitude. A 1 km cell holds 50,000 businesses in Manhattan and zero in the Nevada desert. So query cost is wildly uneven, the dense cells become hot partitions, and there's no single cell size that works everywhere. Uniform grids are the right answer only when your data is genuinely uniform — sensor grids, game maps, tiled imagery.

## Option 2 — Geohash

Recursively bisect the world, alternating longitude and latitude, and record each choice as a bit. Encode the bits in base32.

```
Level 1: is it east or west of the prime meridian?   → 1 bit
Level 2: north or south of the equator?              → 1 bit
Level 3: east or west within that quadrant?          → 1 bit
… interleaving longitude and latitude bits …

37.7749, -122.4194  →  9q8yyk8ytpxr
```

The property that makes it useful: **a shared prefix means physical proximity.** `9q8yy` is a box containing `9q8yyk`, which contains `9q8yyk8`. So precision is just string length:

| Length | Cell size (approx) |
|---|---|
| 4 | 39 km × 20 km |
| 5 | 4.9 km × 4.9 km |
| 6 | 1.2 km × 0.6 km |
| 7 | 153 m × 153 m |
| 8 | 38 m × 19 m |

A radius query picks the length whose cell is comparable to the radius, then does a **prefix match** — which an ordinary B-tree or any key-value store handles natively:

```sql
SELECT id FROM places WHERE geohash LIKE '9q8yy%';
```

That's the real appeal: geohash needs **no special index type and no special database.** It works in Postgres, MySQL, Redis (sorted sets), DynamoDB, or a plain sorted file.

### The boundary problem

Geohash's weakness is specific and must be handled, or your results are wrong rather than slow:

**Two points can be metres apart and share no prefix at all.** A point just east of the prime meridian and one just west differ in the *first* bit, so their geohashes diverge immediately. The same happens at every cell boundary at every level — and a user standing near an edge would see nothing on the other side of it.

```
     9q8yv │ 9q8yy
   ────────┼────────      A user at the ✚ is metres from businesses in
     9q8yt │ 9q8yw        four different cells sharing no useful prefix.
           ✚
```

The fix is to **always query the cell plus its eight neighbours**, then filter by true distance:

```
cells = [my_geohash] + neighbours(my_geohash)     # 9 prefix queries
candidates = union(lookup(c) for c in cells)
results = [p for p in candidates if haversine(me, p) <= radius]   # exact filter
```

Nine lookups instead of one, and the final exact-distance filter is mandatory regardless — a geohash cell is a rectangle, and you asked for a circle.

The converse also holds and surprises people: **a long shared prefix doesn't guarantee closeness.** Two points at opposite corners of one cell share the whole prefix and can be at the cell's diagonal apart.

## Option 3 — Quadtree

A tree that recursively splits a region into four quadrants, but **only where the data is dense**:

```
Split any node holding more than N points (say 100) into four children.
Repeat until every leaf holds ≤ N.

  Manhattan  → subdivided ~15 levels deep, tiny leaves
  Nevada     → one leaf covering hundreds of km
```

This is the direct fix for the uniform grid's problem: **cell size adapts to density**, so every leaf holds roughly the same number of points and query cost is uniform everywhere.

It also supports a query geohash cannot answer well: **k-nearest-neighbour.** "The 5 closest petrol stations" needs no radius — descend to the query point's leaf, then walk outward through sibling nodes until you have 5 and can prove no unvisited node could contain a closer one. With geohash you'd have to guess a radius, and widen it if you found too few.

**The costs are operational rather than algorithmic:**

- It's an **in-memory tree**, built at process start. Building one for 200 million points takes minutes (O(n log n)), during which the server can't serve traffic — so a deploy has to roll out incrementally across the fleet.
- **Updates are awkward.** Adding a point can overflow a leaf and force a split; removing points can leave the tree unnecessarily deep. Doing it in place needs locking; the common alternative is to rebuild periodically and accept staleness.
- It's **not a database index.** You're maintaining a data structure in application memory, with all the deployment and consistency questions that implies.

## Option 4 — S2 (Hilbert curve) and H3

**S2** projects the sphere onto the six faces of a cube, then orders cells along a **Hilbert curve** — a space-filling curve with much better locality than geohash's Z-order interleaving.

```
Z-order (geohash):  ┌─┐ ┌─┐     Hilbert:   ┌─┐ ┌─┐
                    │ └─┘ │                │ └─┘ │
                    └─┐ ┌─┘                └─────┘     fewer long jumps
```

Two genuine advantages over geohash:

**Better locality.** A Z-order curve makes long jumps across space at every power-of-two boundary; a Hilbert curve never jumps — consecutive cells always touch. That means a region maps to **fewer contiguous ranges**, so a query becomes fewer index range scans. The boundary problem is reduced (not eliminated — no 1D ordering of 2D space can eliminate it; that's a topological fact, not an implementation gap).

**Region covering at mixed levels.** S2 can cover an arbitrary shape — a delivery zone, a city boundary, a geofence — with a set of cells at *different* levels: big cells for the interior, small ones along the edges. You ask for "at most 20 cells, between level 8 and level 14" and get an efficient approximation. Geohash's fixed-length cells can't do this, which is why S2 is the standard choice for **geofencing**.

**H3** (Uber's system) uses **hexagons** instead of squares. The reason is neat: a hexagon's six neighbours are all equidistant from its centre, whereas a square's eight neighbours are at two different distances (edge-adjacent versus corner-adjacent). That makes distance approximations and flow calculations more uniform — which matters when you're modelling movement across cells rather than just looking things up. The catch is that hexagons don't tile a sphere perfectly (you need 12 pentagons), and hexagons don't subdivide exactly into smaller hexagons, so the hierarchy is approximate.

## Choosing

| | Uniform grid | **Geohash** | **Quadtree** | **S2 / H3** |
|---|---|---|---|---|
| Adapts to density | No | No | **Yes** | Partly (mixed-level covering) |
| Needs a special index | No | **No** — prefix match | Yes, in-memory tree | No — integer ranges |
| Update cost | O(1) | **O(1)** | Expensive (splits/rebuild) | O(1) |
| k-nearest-neighbour | Poor | Poor | **Good** | Good |
| Geofencing / arbitrary regions | Poor | Poor | Fair | **Good** |
| Boundary problem | Yes | Yes — query 9 cells | Less severe | Reduced, not gone |
| Implementation effort | Trivial | **Low** | Medium | Medium (use a library) |
| Used by | Tiled imagery, games | Redis, Elasticsearch, many APIs | Classic GIS, in-memory search | Google Maps, Uber (H3), Foursquare |

**The practical rule:**

- **Static or slowly-changing points, plus a normal database** → **geohash.** It needs no new infrastructure, and the nine-cell query plus an exact-distance filter is a dozen lines. This is the right default and it's what the [proximity service](../case-studies/proximity-service/00-overview.md) case study uses.
- **Rapidly moving points** → **geohash again, or a plain grid.** The deciding factor flips: what matters is that an update is O(1), and a quadtree's rebuild cost makes it a poor fit for data that changes every few seconds. The [nearby friends](../case-studies/nearby-friends/00-overview.md) case study leans on this.
- **k-nearest queries over static data, in memory** → **quadtree.**
- **Geofencing, or heavily skewed density with mixed-precision needs** → **S2** (or **H3** if you're modelling movement between cells).

## Always filter by true distance

Whichever scheme you pick, the index gives **candidates**, not answers. Cells are rectangles (or hexagons); you asked for a circle. So the final step is always an exact distance computation:

```
results = [p for p in candidates if haversine(me, p) <= radius]
```

Use the **haversine** formula for great-circle distance on a sphere. For short distances an equirectangular approximation (scaling longitude by `cos(lat)`) is faster and accurate enough, and it avoids trigonometric functions in a hot loop. Skipping the filter entirely means returning points from the corners of your cells that are outside the requested radius — a correctness bug that's easy to miss because the results still *look* nearby.

## Interviewer follow-ups

**Why can't a composite B-tree index on `(lat, lng)` do this?**
Because a B-tree orders lexicographically, so it can only narrow on the leading column. It finds the latitude band and then scans every row in that planet-wide strip. The index accelerates one dimension; the query needs two. That's the whole reason these schemes exist.

**A user is standing exactly on a cell boundary and sees no results from across the street. What's wrong?**
The query is only looking at one cell. It must query the cell plus its eight neighbours and then filter by true distance. The boundary problem is inherent to mapping 2D onto 1D — you handle it, you don't avoid it.

**How would you handle a hot cell — Times Square with a million points?**
Geohash can't adapt, so either go deeper for that cell specifically (variable-length prefixes with a lookup table mapping cell → precision) or switch to a density-adaptive structure. If the pain is *write* concentration rather than read cost, shard the cell's key by appending a bucket number and scatter-gather on read — the same sharded-counter trick used for [hot accounts](../case-studies/digital-wallet/01-architecture-hld.md#load-handling).

**Objects are moving every few seconds. Does that change your choice?**
Substantially. Update cost now dominates read cost, so a quadtree's rebuild or in-place-split cost disqualifies it. A geohash or plain grid where an update is "recompute one string and write it" is right, ideally in an in-memory store with TTL eviction so stale positions disappear on their own.

**Where does the geospatial index actually live — in the database or in the application?**
Geohash and S2 produce plain sortable values, so they live in whatever store you already have, which is most of their appeal. A quadtree is an in-memory application structure, which means you own its build time, its deployment story, and its consistency with the database it was built from — three problems you don't have with the other two.
