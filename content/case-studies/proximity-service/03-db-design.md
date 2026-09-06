# Module 03 — Database Design & Scaling

![Two tables, two churn profiles: an ephemeral in-memory location store versus a durable, geohash-indexed POI table](diagrams/er.svg)

## From entities to schema

- **Dynamic locations:** `(entity_id, geohash, lat, lng, updated_at)` — in-memory only, entries TTL-expire naturally when an entity stops pinging (an offline driver ages out of "nearby" results without any explicit cleanup job).
- **Static POIs:** `(poi_id, name, category, lat, lng, geohash)` — indexed on `geohash` (cross-ref [Database Indexing](../../database-design/database-indexing.md)'s prefix-matching reasoning: a range scan over sorted geohash strings is what answers "all points starting with this prefix" efficiently).

## Indexes

- **Dynamic store:** no traditional index at all — the in-memory geo structure (e.g. Redis's own sorted-set-backed geo commands) IS the index; there's no separate schema to design here, which is itself a consequence of choosing a store whose entire purpose is fast geo-lookups over ephemeral data.
- **Static store, `geohash` (prefix range):** the primary access path — "all POIs whose geohash starts with this prefix" is a sorted range scan, not a full scan, as long as the column is indexed as a string with left-to-right prefix matching (a plain B-tree index on `geohash` gives this for free).
- **Static store, `(category, geohash)` composite**, once category filtering (Module 02's practice exercise) becomes a real, common query — putting `category` first narrows to a much smaller set before the geohash range scan even runs, the same leftmost-prefix reasoning this guide applies to every composite index: filter by the more selective, equality-matched column first, range-scan the rest.

## Consistency

- **Dynamic store:** no consistency guarantee beyond "reflects a recent ping" — a sharp contrast with this guide's [Payments System](../payments-system/03-db-design.md), where a stale read is a real incident. Here, a position that's a few hundred milliseconds old is invisible to the user, so the store can skip transactions, locks, and durability entirely and still meet every actual requirement.
- **Static store:** strongly consistent for the record itself — a restaurant's address or hours shouldn't flicker between two different values on successive reads — but the read-heavy access pattern makes read replicas and the cache-aside layer from Module 01 an easy, low-risk addition: a cache entry that's a day stale for a restaurant's listing is harmless, unlike a stale price or balance elsewhere in this guide.

## Scaling the schema

- **More geo-shards means finer-grained parallelism** as either store grows — since shard boundaries follow geohash prefixes, adding shards is "split a dense region's cells across more nodes," not a global rebalance, matching the general reasoning in [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md).
- **The two stores scale on entirely different axes:** the dynamic store scales with concurrent moving-entity count and ping frequency (a write-volume problem); the static store scales with total POI count and query volume (a read-volume and index-size problem). Treating them as one scaling problem would obscure which lever actually needs pulling when either one gets slow.

## Connecting it back

Trace the chain from Module 00 through here: the requirement was sub-100ms nearby search over tens of millions of points, with two entity types that have nothing in common except their shape (a 2D coordinate) and everything different about how often they change. That's why Module 01 splits storage by churn rate rather than by entity type; it's why the LLD's 9-cell fetch exists — correctness, not just speed, depends on it; and it's why this schema's one shared idea, the geohash-prefix index, is the mechanical foundation both stores build on even though everything else about them — durability, consistency, scaling axis — is different. One encoding scheme, two completely different tuning stories built on top of it.
