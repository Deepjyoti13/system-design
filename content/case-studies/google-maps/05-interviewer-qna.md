# Module 05 — Interviewer Q&A

---

### 1. How do you store and serve 60 petabytes of map tiles?

The first move is to notice **60 PB is a ceiling, not an estimate.** It comes from summing 4ᶻ across zoom 0–21 — 5.86 trillion tiles at ~10 KB. But most of the Earth is ocean, ice, desert or unbroken forest, so at zoom 21 billions of tiles are **byte-identical**.

So tiles are **content-addressed**:

```
(v42, 21, 300000, 400000) ─┐
(v42, 21, 300001, 400000) ─┼─→ hash 3f8a91… → ONE stored object
(v42, 21, 300002, 400000) ─┘
```

One indirection, three payoffs: storage collapses by orders of magnitude; a new map version costs only the tiles that actually changed (v42 shares every unchanged hash with v41); and the CDN caches one hot ocean tile instead of billions of distinct cold objects.

Two more reductions: **don't generate depth where there's no detail** — cities go to zoom 21, open ocean stops around zoom 8 and deeper requests are served by upscaling something featureless. And **the CDN does the actual serving** at a ~99% hit rate, so the origin sees roughly 1% of tile traffic.

The framing I'd lead with: **the largest dataset in this system needs the least machinery.** 60 PB is object storage plus a CDN — no database, no application logic on the read path. Meanwhile the 100 GB geocoding dataset needs the most sophisticated engine in the design. That inversion is the useful thing to establish early.

---

### 2. Why precompute tiles instead of rendering on demand?

On-demand rendering fails for two reasons, and the second is fatal.

**It puts compute in front of immutable data** — the map of San Francisco doesn't change between requests, so you'd recompute an identical answer millions of times a day.

**It destroys cacheability.** An on-demand render is keyed by an arbitrary bounding box, and no two users request exactly the same box. So the hit rate is ~0 and **every request reaches the origin.** At 1 billion DAU that isn't a scaling problem you can buy your way out of.

Tiling **quantizes space into a fixed grid**, so everyone looking at the same area requests the *same* tile and the answer never changes. That's what converts a compute problem into a static-asset problem — and static assets at global scale is a solved problem called a CDN.

Two properties fall out that are worth mentioning: a viewport is ~40 small independent parallel requests, so a **partially-loaded map still renders** and fills in progressively on a bad connection; and immutability means an edge can cache with `max-age=31536000, immutable` and never revalidate.

---

### 3. Vector or raster tiles?

**Vector**, and the bandwidth saving isn't actually the strongest argument.

Vector tiles are **5–10× smaller** (5–20 KB versus 20–100 KB), which directly serves the battery-and-data requirement. That's the obvious win.

The bigger one: **one tile set serves every style and every language.** Dark mode, satellite overlay, transit emphasis, high-contrast accessibility, and 80 languages of labels are all client-side style rules applied to the same geometry. Under raster tiling each of those is a **separate 60 PB tile set.** So vector tiles don't just save bandwidth — they avoid multiplying the largest dataset in the system by the number of visual variants, which is what makes those variants economically possible at all.

Also: vector geometry **scales smoothly between zoom levels**, so pinch-zoom is continuous rather than a blurry upscale until the next tile set loads.

The honest cost is client CPU and GPU, which partially fights the battery requirement it serves — you save radio energy and spend rendering energy. On modern hardware the radio dominates so it's clearly favourable, but it's a real trade, and very old devices get a worse experience.

---

### 4. Roads change. How do you update tiles that are cached at thousands of edges?

You don't invalidate — you **version**.

```
/v41/{z}/{x}/{y}.mvt     ← current
/v42/{z}/{x}/{y}.mvt     ← published alongside; NOTHING invalidated

GET /v1/map/version → { "tiles_version": 42 }    ← the one thing clients poll
```

Purging is not merely inefficient here, it's **operationally infeasible**: you'd have to enumerate affected tiles across trillions of objects, issue purges to every PoP, and wait for eventual propagation with no reliable completion signal.

Versioning gives three properties purging can't:

- **No moment of inconsistency.** A client is always on exactly one coherent version. Clients on v41 and v42 are both correct.
- **Rollback is a pointer change.** Set the version endpoint back to 41 — nothing needs re-uploading or re-warming, because v41 is still cached everywhere. Compare a bad purge-based publish, where recovery means re-purging and re-warming trillions of objects from a cold origin.
- **A new version is cheap**, because content-addressed dedup means v42 shares every unchanged tile with v41.

The real cost: **v42 starts cold at the edges**, so a publish causes an origin load spike as popular tiles are re-fetched. Staged rollout of the version endpoint by region turns that spike into a ramp.

---

### 5. The road graph is only 10 GB. Why not just load it and run Dijkstra?

Because the problem is **search cost, not storage** — and that distinction is the heart of the routing design.

A\* expands nodes outward from the origin. For a 600 km route the frontier grows to millions of nodes, each expansion a pointer-chase into a 10 GB structure, so essentially every one is a cache miss at ~100 ns. Millions of those is seconds of pure memory-latency stall.

Worse, **cost grows with the geographic area searched, not with route length.** Every minor residential street in every town between here and there gets considered, to produce a route that uses none of them.

Two techniques attack exactly that:

**Routing tiles** cut the graph geographically. The search loads a tile, traverses it, hits a boundary node, fetches the neighbour and stitches it in. So the working set is the corridor actually traversed.

**Hierarchy** is what makes long routes fast. The graph exists at multiple detail levels — level 0 is every road, level 3 is motorways only. A long route is three phases: escape level 0 near the origin, **cruise across the country on level 3** (thousands of edges, not tens of millions), then drop back to level 0 near the destination.

The middle phase is a three-to-four-order-of-magnitude reduction, and it matches the correct answer — a 600 km route *does* use motorways in the middle, so searching residential streets wasn't just expensive, it was **searching a space the answer isn't in.**

The honest limitation: hierarchical routing is an **approximation**. It can miss a marginally faster route via a clever sequence of arterial roads, because the coarse pass never considered them. Mitigated by overlapping levels and widening the search near level junctions, and ultimately accepted — the requirement is correct instructions, not provably minimal time.

---

### 6. Why is ETA a separate service instead of edge weights in the routing tiles?

Because of a clean separation of change rates:

```
The GRAPH   (which roads exist, how they connect)  changes RARELY   — days/weeks
The WEIGHTS (how long each takes right now)        change CONSTANTLY — seconds
```

Baking weights into tiles would mean **republishing routing tiles every few seconds** — the equivalent of publishing a new map version continuously, which defeats the immutability that makes tiles cacheable at all.

So tiles carry static properties (geometry, speed limit, road class, turn restrictions) plus a `segment_id`, and live travel time is looked up separately by that id. Three benefits: the ML model retrains and redeploys on its own cadence; tiles stay immutable; and if ETA is unavailable, navigation **falls back to speed limits and historical averages** — routes stay correct, only the time estimate degrades. That's the ideal degradation shape: lose the enhancement, keep the function.

One detail worth volunteering: the pathfinder generates **2–3 candidate routes using cheap approximate costs**, and only then applies the expensive ML model to those few. Running inference *inside* the search would mean a model call per edge expansion — millions of them. Cheap retrieval then expensive ranking is the same shape the [search engine](../search-engine/00-overview.md) case study uses.

And historical data isn't optional: a route requested for 8am tomorrow has no live traffic to consult, so the model *must* have per-segment historical patterns by time-of-day. That's what `segment_speed_history` exists for, and it's why the ETA service can't just be a live-speed lookup.

---

### 7. 694,000 GPS samples per second. How do you not fall over?

**Batch on the client, and acknowledge before storing.**

Fifteen samples per request — 75 seconds of movement — cuts request volume 15×, from 694k/sec to 46k/sec (231k at peak).

But the real reason for batching isn't capacity, it's **battery**. On a phone the cellular radio is usually the largest single power consumer, and it's the *wake-ups* that cost, not the bytes. One request per minute versus twelve is the difference between a radio that sleeps and one that's continuously awake. [Module 00](./00-overview.md#requirements) makes battery frugality a first-class requirement, and this is where it shows up.

Then: **`202 Accepted`, not `200 OK`.** The server takes the batch and returns immediately, before touching Cassandra or Kafka. That's the honest status code for fire-and-forget, and it's defensible because a GPS sample is **worthless within a minute, self-corrected by the next batch, and statistical in purpose** — traffic speed is an aggregate over thousands of vehicles, so losing one vehicle's batch changes no number anyone sees. Same self-correcting-data argument as [nearby friends](../nearby-friends/00-overview.md#what-dropped-points-are-acceptable-buys).

Storage is Cassandra partitioned on `(user_id, bucket)` — day-bucketed so partitions stay bounded — with Kafka in front to decouple ingest latency from write latency and provide a replay buffer.

The point I'd close on: **2.4 TB/day of raw traces exists to produce a ~2 TB aggregate table that never grows.** `segment_speed_history` (segment × day-of-week × 15-minute bucket) is what the product actually consumes. Everything upstream is scaffolding, which is why raw traces get a 90-day TTL.

---

### 8. An accident closes a motorway. How do you find and reroute the affected drivers?

The naive approach stores each user's route as its full tile list and scans for the affected tile — millions of navigators × hundreds of tiles each is a billion-row scan per incident.

**The trick is storing the route at multiple resolutions.** Because coarse tiles *contain* fine tiles, a whole route summarizes to a handful of progressively coarser tiles:

```
user_1: r_1, super(r_1), super(super(r_1)), …     # a few entries, not hundreds
```

Now it's a **containment test** against a handful of entries rather than a scan. Does the incident's tile fall inside any of this user's coarse tiles?

The trade is precision: a coarse tile covering a large region matches users passing *near* the incident but not through it. So it's two-stage — **coarse containment to shortlist, then recompute to confirm.** Filter-then-verify, the same shape as the [geospatial boundary problem](../../hld-building-blocks/geospatial-indexing.md#the-boundary-problem).

Two things I'd raise unprompted, because they're where this gets genuinely unsolved:

**The recomputation cost is unbudgeted.** A major motorway closure shortlists tens of thousands of navigators, all needing a fresh route computation simultaneously. [Module 01](./01-architecture-hld.md#what-youd-revisit-as-this-grows) names this as the design's least-solved piece.

**There's a feedback loop.** Every reroute sends traffic onto the same alternative, which the traffic aggregator then observes as newly congested — potentially rerouting everyone again. And because the ETA model is trained on speeds that were themselves shaped by past routing decisions, **the system is partly predicting its own behaviour.** Real deployments deliberately split reroutes across several alternatives to damp this. That mechanism isn't in this design.

Finally, a reroute is only *offered* if materially better — interrupting a driver to save 40 seconds is a worse product than silence.

---

### 9. What happens to a driver mid-journey when the navigation service goes down?

**Nothing.** The route was returned in full — every step, the complete polyline, all instructions — and the client holds it.

So mid-journey navigation needs **no server contact at all**: the phone matches its GPS against the stored polyline, decides which instruction is next, and speaks it locally.

Three things that buys:

- A tunnel, a dead zone, or a full service outage doesn't interrupt an in-progress journey.
- Turn announcements have **zero network latency** — which matters, because "turn right in 100 metres" is useless three seconds late.
- The radio stays asleep between location batches.

The server is needed only to *start* a journey or to *offer* a reroute, and both are interruptible without harm. That's a rare property worth naming explicitly: **the most latency-sensitive part of the product has been moved entirely off the network.**

It's also half of offline navigation. The other half is pre-downloading map and routing tiles for a region plus doing pathfinding on-device — a substantially different client architecture that isn't designed here.

While we're on degradation, the ordering across the whole system is worth knowing: ETA down → routes with static weights (correct directions, approximate times). Traffic aggregator down → weights go stale gradually. Ingest down → **zero immediate impact**, visible only hours later. Geocoding down → address search fails but coordinate navigation works. **CDN down is the worst failure** — the origin gets hammered and rendering degrades badly, mitigated by multiple CDN providers with DNS failover.

---

### 10. Make this multi-region. What breaks?

Almost nothing, and the reason is worth stating because it's the opposite of most systems in this guide: **no datum in this design requires strong consistency anywhere.**

Every piece of data is either **immutable** (map tiles, routing tiles — versioned, never mutated), **statistical** (traffic speeds — aggregates where two independent computations produce the same answer, which is why the aggregator needs no leader election), or **self-correcting** (GPS positions — superseded within a minute).

So: tiles are global and edge-cached by nature. Routing tiles for a region are served from that region. Location history and traffic aggregates are homed regionally. Geocoding is replicated. **There is no cross-region write coordination anywhere.**

Contrast the [digital wallet](../digital-wallet/06-interviewer-qna.md), where multi-region is the hardest unsolved problem — because there, two accounts must change together and the system has a global invariant (total money) to protect. Here there is no invariant to violate, so geographic distribution is a deployment decision rather than a design problem.

The one wrinkle is **tile versioning assuming a single global version.** A road change in Tokyo currently means publishing a version that Brazil then re-warms from cold. Per-region versioning is probably right and it breaks the tidy single version endpoint — [Module 04](./04-db-design.md#what-youd-revisit-as-this-grows) flags it, and satellite imagery (which updates per-region as new photography arrives) forces the issue.

The genuinely uncomfortable gap I'd raise instead: **2.4 TB/day of precise per-user location traces retained for "traffic modelling."** What the product needs is the ~2 TB aggregate. A 90-day TTL on raw traces is in the schema, but there's no erasure path designed — a deletion request would have to reach Cassandra (where deletes are tombstones that don't reclaim space until compaction), the Kafka backlog, the warehouse, and any archive. That's a privacy liability well beyond the stated purpose, and it's the thing I'd fix before optimizing anything.
