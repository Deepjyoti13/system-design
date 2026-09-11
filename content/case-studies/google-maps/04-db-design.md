# Module 04 — Database Design

![Four stores for four shapes: content-addressed tile blobs, keyed routing-tile blobs, a write-optimized GPS history table, and a small inverted index for geocoding](diagrams/er.svg)

The striking thing about this schema is that **three of the four stores are not databases**, and the one relational store holds the smallest dataset. Recognising that is most of the design.

## Four stores, four shapes

| | Map tiles | Routing tiles | Location history | Geocoding index |
|---|---|---|---|---|
| **Volume** | **~60 PB nominal** (far less deduped) | ~10 GB | **2.4 TB/day** | ~100 GB |
| **Mutability** | Immutable per version | Immutable per version | **Append-only** | Rebuilt in batch |
| **Access** | Key lookup by `(z,x,y)` | Key lookup by `(geohash, level)` | Bulk write; batch read | **Fuzzy text search** |
| **Write rate** | Batch, per map version | Batch, per map version | **694k samples/sec** | Batch |
| **Read rate** | Enormous, but ~99% at the CDN edge | High, served from memory | ~zero interactive | Moderate |
| **Engine** | **Object storage + CDN** | **Object storage + in-memory cache** | Cassandra, via Kafka | Inverted index (Elasticsearch-class) |
| **Is it a database?** | No | No | Sort of | No |

The mismatch on every row is why one store would be wrong. And the inversion from [Module 00](./00-overview.md#capacity-estimation) shows up again: the **60 PB dataset needs the least machinery** (dumb blob storage plus a CDN) while the **100 GB dataset needs the most sophisticated engine** (fuzzy multilingual text matching).

## Map tiles: content-addressed blobs

Not a table. A key-value mapping plus a blob store:

```
# The index: which content does this tile coordinate point at?
tile_index:  (version, z, x, y)  →  content_hash

# The store: one object per DISTINCT tile content
blobs:       content_hash        →  bytes (Mapbox Vector Tile, protobuf)
```

**The indirection through `content_hash` is the whole point.** [Module 02](./02-map-rendering.md#deduplicate-uniform-tiles) established that most of the Earth is featureless at high zoom, so billions of tiles are byte-identical:

```
(v42, 21, 300000, 400000) ─┐
(v42, 21, 300001, 400000) ─┼─→ 3f8a91… → ONE stored object ("empty ocean")
(v42, 21, 300002, 400000) ─┘
```

Three separate benefits, and the third is the one people miss:

1. **Storage collapses** by orders of magnitude — the nominal 60 PB is a ceiling, not an estimate.
2. **A new map version costs only what changed.** v42 shares every unchanged `content_hash` with v41, so publishing a version is a small delta rather than a 60 PB copy. That's what makes the versioning-over-purging strategy in [Module 02](./02-map-rendering.md#updating-the-map-without-purging) affordable.
3. **The CDN caches better.** One hot ocean tile is a single cached object at each edge, instead of billions of distinct cold objects — directly attacking the cold-tile problem from [Module 01](./01-architecture-hld.md#load-handling).

**Why object storage and not a database.** The access pattern is "fetch this immutable blob by key", and the objects are large-ish and never queried by attribute. A database would layer query planning, transactions, and indexing over what is fundamentally a file read — and no relational or document store is designed for trillions of rows of opaque bytes. Cross-ref the [object storage](../object-storage-s3/00-overview.md) case study, which is precisely the system this needs.

In practice `tile_index` isn't even a live lookup on the read path: the CDN URL encodes `(version, z, x, y)` directly and the origin resolves it. So the index is consulted only on the ~1% of requests that miss the edge.

## Routing tiles: keyed graph blobs

```
routing_tile:  (version, geohash, detail_level)  →  bytes

# Inside the blob, a compact binary encoding:
#   nodes[]:     intersection id, lat, lng
#   edges[]:     from_node, to_node, length_m, road_class, speed_limit,
#                turn_restrictions, segment_id  ← the join key to live traffic
#   boundary[]:  neighbouring tile references + the nodes where roads cross out
```

**Why not a graph database**, which is the intuitive answer for graph data. Because there is no graph *query* to run. The navigation service doesn't ask "find me a path" of the store — it asks "give me tile 9q8yy at level 3" and runs [its own A\* in memory](./03-navigation.md#hierarchy-is-what-actually-makes-long-routes-fast). A graph database's value is server-side traversal, and here traversal must happen in the application because it needs custom heuristics, level transitions, and per-request user filters. So you'd be paying for a traversal engine you can't use.

The blob is a **compact binary format, not JSON**. Edges are fixed-width records so the in-memory representation is close to the on-disk one — no parse step, and adjacency can be an array index rather than a pointer chase. That matters because [Module 03](./03-navigation.md#the-problem-isnt-size-its-search) found the search is bounded by memory latency, not by graph size, and cache-friendly layout is the direct lever on that.

**`segment_id` is the join key that makes the ETA separation work.** The tile carries static properties only; live travel time is looked up by `segment_id` from the traffic store. This is what lets weights change every few seconds while tiles stay immutable — the separation of change rates from [Module 03](./03-navigation.md#eta-is-a-separate-service-on-purpose), expressed as a foreign key.

## Live traffic: an in-memory key-value store

```
KEY    speed:{segment_id}
VALUE  (current_speed_mps: float32, sample_count: uint16, updated_at: uint32)
TTL    900 seconds
```

Small (100M segments × ~16 bytes ≈ **1.6 GB**), read on every route computation, written continuously by the traffic aggregator.

**The TTL is doing real work**, the same triple duty as in [nearby friends](../nearby-friends/03-db-design.md#live-location-redis): it bounds memory, bounds staleness, and — most usefully — makes "no recent data for this segment" an *automatic* state rather than something to detect. When a segment's key expires, the ETA service falls back to historical or speed-limit-based estimates without any explicit "is this stale?" check.

`sample_count` is carried because a speed derived from 3 vehicles deserves less confidence than one from 300, and the ETA model weights it accordingly. Storing the aggregate without its sample size would throw away the information needed to know whether to trust it.

## Location history: Cassandra

```sql
CREATE TABLE location_history (
    user_id      bigint,
    bucket       int,          -- day number: bounds partition size
    ts           timestamp,
    lat          float,
    lng          float,
    accuracy_m   smallint,
    speed_mps    float,
    heading_deg  smallint,
    PRIMARY KEY ((user_id, bucket), ts)
) WITH CLUSTERING ORDER BY (ts DESC)
  AND compaction = { 'class': 'TimeWindowCompactionStrategy' }
  AND default_time_to_live = 7776000;      -- 90 days
```

**Why Cassandra.** 694,000 appends/sec with essentially no interactive reads — the most write-skewed workload in the system. LSM storage makes every write a sequential memtable append; a relational store would spend its life on random page updates and index maintenance for rows nobody queries. Cross-ref [SQL vs NoSQL](../../database-design/sql-vs-nosql.md).

**Why `(user_id, bucket)` and not `user_id` alone.** An active user contributes ~12 samples/minute, so ~17,000/day and millions per year. Cassandra partitions are the unit of storage *and* of repair, so an unbounded partition becomes an operational problem long before it becomes a storage one. The day bucket bounds every partition by construction; the cost is that a multi-day query reads several partitions, which is fine for a workload with no interactive reads.

**`float` is correct here**, unlike money in the [digital wallet](../digital-wallet/05-db-design.md#why-amount-minor-is-an-integer). A `float32` resolves latitude to ~1 metre, well below consumer GPS accuracy (3–10 m in the open, far worse among buildings). **The measurement error dwarfs the representation error**, so precision loss is unobservable — whereas money has no measurement error, which makes representation error the only error there is.

**`default_time_to_live = 7776000`** — and this line is a design position, not a default. [Module 01](./01-architecture-hld.md#what-youd-revisit-as-this-grows) notes that what the product actually needs from this data is *aggregate speed per segment per time-of-day*, which is a tiny derived table. Retaining per-user precise traces indefinitely would be a privacy liability far exceeding the stated purpose, so the raw traces expire and the aggregates persist. TWCS makes that expiry drop whole SSTables rather than rewriting them — the same "delete a file, not a row" principle as the [message queue's segments](../distributed-message-queue/02-storage-engine.md#segments-and-why-the-log-isnt-one-file).

**Kafka in front** decouples ingest latency from Cassandra's write path and gives a replay buffer, so a Cassandra outage becomes a delay rather than a gap.

## Traffic aggregates: the derived table that matters

```sql
CREATE TABLE segment_speed_history (
    segment_id   bigint,
    day_of_week  smallint,      -- 0-6
    time_bucket  smallint,      -- 15-minute bucket, 0-95
    p50_speed    float,
    p85_speed    float,
    sample_count int,
    PRIMARY KEY (segment_id, day_of_week, time_bucket)
);
```

100M segments × 7 days × 96 buckets ≈ 67 billion rows at ~30 bytes ≈ **2 TB** — total, not per day.

**This is the actual product of the location pipeline**, and it deserves the emphasis: 2.4 TB/day of raw traces exist to produce a 2 TB table that never grows. Everything upstream is scaffolding.

Two reasons it's essential rather than a nice-to-have:

- **Future routes need it.** A route requested for 8am tomorrow has no live traffic to consult, so the ETA model reads historical patterns. Live speed alone cannot answer "how long will this take next Tuesday".
- **It's the fallback** when a live segment's TTL has expired, which is the common case for minor roads with few sampled vehicles.

`p85_speed` alongside `p50_speed` is deliberate: for an ETA people rely on, the pessimistic percentile is often the more useful number — an estimate that's right half the time is worse than one that's conservative.

## Geocoding: an inverted index

```
"1600 Amphitheatre Parkway, Mountain View, CA"
   → tokenize, normalize ("Pkwy"→"Parkway", "Mtn View"→"Mountain View", case, accents)
   → inverted index lookup + fuzzy matching for typos
   → candidate places, ranked by prominence + query proximity
   → (lat, lng), place_id, precision level (ROOFTOP | RANGE_INTERPOLATED | GEOMETRIC_CENTER)
```

**Why a text-search engine and not a relational table.** The query is fuzzy, multilingual, abbreviation-tolerant, and typo-tolerant. `WHERE address = ?` answers approximately none of the real queries people type. This is squarely an inverted-index problem — cross-ref [Search & Inverted Indexes](../../scalability-resilience/search-inverted-indexes.md) and the [search engine](../search-engine/00-overview.md) case study.

**Reverse geocoding is the opposite problem** and needs a different index entirely: given a point, find the nearest addressable feature. That's a spatial query, so it uses an **R-tree or S2 cell index** over address points and street geometry — cross-ref [Geospatial Indexing](../../hld-building-blocks/geospatial-indexing.md). Two directions of one product feature, two unrelated index structures, and conflating them is a common design error.

`precision level` is returned because it's honest and actionable: "ROOFTOP" means the exact building; "RANGE_INTERPOLATED" means interpolated along a street segment and could be tens of metres off. A navigation client should route to the street for the latter rather than pretending to a precision it doesn't have.

Geocoding results are **heavily cached** — the same addresses are searched repeatedly, so a plain LRU absorbs most traffic.

## Indexes

| Index | Serves |
|---|---|
| `(version, z, x, y) → content_hash` | Tile origin resolution, on the ~1% of requests that miss the CDN. |
| `content_hash → bytes` | The deduplicated blob store. |
| `(version, geohash, detail_level)` | Routing tile fetch during pathfinding. |
| `speed:{segment_id}` (in-memory) | Live edge weights, read per route computation. |
| `PRIMARY KEY ((user_id, bucket), ts DESC)` on `location_history` | Bulk writes distributed by user; bounded partitions; batch reads newest-first. |
| `PRIMARY KEY (segment_id, day_of_week, time_bucket)` on `segment_speed_history` | The ETA model's historical lookup — a point read per segment. |
| Inverted index on normalized address tokens | Forward geocoding. |
| S2/R-tree index on address points | **Reverse** geocoding — a different structure for the inverse query. |

**Deliberately absent:** no secondary index on `location_history` (high-cardinality secondary indexes are a Cassandra anti-pattern, and there are no interactive queries to serve); no index on tile *content* (nobody searches tiles by what's in them); no spatial index over live vehicle positions (the system never asks "who is near this point" — traffic is aggregated per segment, and the client already knows where it is).

## Consistency

| Data | Model | Why |
|---|---|---|
| Map tiles | **Immutable per version; eventually consistent across edges** | A client is always on exactly one coherent version, so there is no torn state. Version propagation lag just means some clients are on v41 and some on v42 — both correct. |
| Routing tiles | **Immutable per version** | Same. A route is computed entirely within one version, so it can't mix graphs. |
| Live traffic speeds | **Eventual, seconds; last-write-wins** | An aggregate over a time window, so two aggregators computing it produce the same value — which is why no leader election is needed. |
| Location history | **Eventual, at-least-once** | Kafka gives at-least-once; a duplicate row with the same `(user_id, bucket, ts)` is an idempotent overwrite. **Duplication is harmless by construction**, so exactly-once would be wasted effort. |
| Traffic aggregates | **Eventual, batch** | Recomputed periodically; a slightly stale historical average is indistinguishable from a current one. |
| Geocoding index | **Eventual, batch-rebuilt** | A new address appearing hours late is acceptable. |
| Route responses | **Read-only snapshot** | Weights are read once at the start of a search, so the route is internally consistent even if the world moved during computation. Re-reading mid-search could produce a self-contradictory path — worse than a slightly stale one. |

**Nothing in this system requires strong consistency anywhere.** That's unusual and worth stating plainly: every datum is either immutable (tiles), statistical (traffic), or self-correcting (positions). It's the deep reason multi-region is easy here and hard in the [digital wallet](../digital-wallet/06-interviewer-qna.md) — there is no global invariant to protect.

## Scaling the schema

**Map tiles** scale by adding CDN capacity and object-storage nodes. Deduplication is what keeps the real footprint manageable; sharding is the object store's problem, and the [object storage](../object-storage-s3/04-db-design.md#scaling-the-schema) case study covers it.

**Routing tiles** are ~10 GB, so they don't need sharding for capacity — they're replicated to every navigation service instance's memory cache, keyed by region so a service in Europe holds European tiles hot.

**Location history** scales linearly on `(user_id, bucket)`: high-cardinality partition key, so writes distribute evenly with no hotspot. The 90-day TTL bounds total size at ~215 TB rather than letting it grow forever.

**Traffic aggregates** are a fixed ~2 TB regardless of traffic volume, because the row count is determined by `segments × days × buckets` and not by sample count. That's a genuinely pleasant property: the derived table doesn't grow with usage.

**Geocoding** is ~100 GB, sharded by region or by index shard, and heavily replicated for read throughput.

**Multi-region** is the easy case, as the consistency table implies. Tiles are global and edge-cached by nature; routing tiles for a region are served from that region; location history and traffic are homed regionally; geocoding is replicated. **No cross-region write coordination exists anywhere in the design.**

## Connecting it back

**"~60 PB of map tiles"** (Module 00) → precompute immutable tiles with client-computable URLs and serve from a CDN (Modules 01–02) → which makes updates a *new version* rather than a purge → surfacing here as **content-addressed blobs**, where dedup collapses the real footprint *and* makes a new version cost only the tiles that changed, *and* makes an ocean tile one hot cached object instead of billions of cold ones. One indirection, three payoffs.

**"Routes in under a second over 100M edges"** (Module 00) → geographic routing tiles at multiple detail levels, searched in memory (Module 03) → surfacing here as **compact fixed-width binary blobs in object storage rather than a graph database**, because there's no server-side traversal to delegate, and because cache-friendly layout is the direct lever on a memory-latency-bound search.

**"ETA accounting for current traffic"** (Module 00) → separate the rarely-changing graph from the constantly-changing weights (Module 03) → surfacing here as **`segment_id` as a join key**: tiles carry static properties, `speed:{segment_id}` carries live values with a TTL, and `segment_speed_history` carries the historical fallback that future-dated routes require.

**"Data and battery frugality"** (Module 00) → vector tiles (5–10× smaller) and batched location updates (15× fewer requests) (Modules 01–02) → surfacing here as **`202 Accepted` semantics** making fire-and-forget storage legitimate, and as a 90-day TTL on raw traces because the 2 TB aggregate is what the product actually consumes.

## What you'd revisit as this grows

- **`segment_speed_history` has a feedback loop nobody has modelled.** It's trained on observed speeds, and those speeds were shaped by past routing decisions — so the model partly predicts its own behaviour. Routing everyone around congestion creates congestion on the alternative, which the next aggregation observes as newly slow. Real deployments split traffic across alternatives to damp this, and that mechanism doesn't exist in this schema.

- **The 90-day TTL is a policy assertion with no enforcement beyond Cassandra.** A deletion request must also reach the Kafka backlog, the analytics warehouse, and any object-storage archive. Cassandra deletes are tombstones that don't reclaim space until compaction, so "deleted" and "gone" differ by a compaction cycle. None of that is designed.

- **No place/POI data at all.** Businesses, opening hours, reviews and photos are a large relational-plus-search dataset that a real Maps product needs, deliberately scoped out to the [proximity service](../proximity-service/00-overview.md). The seam between them (a `place_id` returned by geocoding that the proximity service resolves) is asserted rather than designed.

- **Turn restrictions and road attributes are underspecified.** The routing tile carries `turn_restrictions`, and real routing needs far more: time-of-day restrictions, vehicle-class restrictions (weight, height, hazmat), seasonal closures, and bus lanes. Each expands the edge record and some of them make edge weights *conditional on the query*, which complicates the level-hierarchy assumption that a coarse tile's weights are usable by any route.

- **Tile versioning assumes one global version.** [Module 02](./02-map-rendering.md#practice-extend-it-yourself) raises satellite imagery updating per-region, and the same applies to road data: a road change in Tokyo shouldn't require republishing a version that Brazil then re-warms. Per-region versioning is probably right and it breaks the single tidy version endpoint.
