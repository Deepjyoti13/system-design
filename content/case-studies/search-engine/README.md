# Design a Search Engine

![Query scatter-gather across term-partitioned index shards, merged and ranked before returning](diagrams/hld.svg)

## Requirements

**Functional:**
- Crawl the web and keep an index of pages fresh as they change.
- Given a text query, return a ranked list of the most relevant pages within a tight latency budget.
- Support basic query operators (phrase match, exclusion) — full natural-language understanding is out of scope for this pass.

**Non-functional** (stated as assumptions, interview-style):
- 10 billion pages indexed.
- 100,000 search queries/sec average across all users.
- p99 query latency under 200ms — a query answered slowly is worse than one answered slightly-stale, so serving latency wins over serving freshness whenever the two trade off.
- Pages don't need to appear in results the instant they're crawled; a lag of hours to a day between crawl and searchability is acceptable for most of the web, with a faster path reserved for high-value/rapidly-changing sources.

## Capacity Estimation

Using this guide's [back-of-envelope method](../../foundations/back-of-envelope-estimation.md):

- **Query rate:** 100,000/sec average. At a 3x peak factor (time-zone-clustered daytime usage): **~300,000/sec peak.**
- **Index size:** assume an average page contributes roughly 2KB of indexed terms (postings, not the raw page). 10B pages x 2KB = **~20TB** for the core inverted index — large enough that it cannot live on one machine, which is the whole reason sharding is this design's centerpiece, not an afterthought.
- **Crawl rate:** to fully refresh 10B pages on, say, a 30-day cycle: 10B / (30 x 86,400) ≈ **~3,900 pages/sec** sustained crawl throughput (cross-ref [Web Crawler](../web-crawler/README.md) for the politeness/dedup mechanics that actually deliver this rate).
- **Query fan-out cost:** each incoming query, if the index is sharded N ways, becomes N shard-level lookups — at 300,000 peak queries/sec and, say, 100 shards, that's 30M shard-level operations/sec across the fleet, which is the real number the serving tier has to be provisioned against, not the 300,000 client-facing figure.

## Approach Walkthrough

A page becomes searchable through three stages that run at very different speeds and don't block each other: crawling (continuous, politeness-limited, [Web Crawler](../web-crawler/README.md)'s job), indexing (turning crawled pages into an inverted index the query path can actually use, [Search & Inverted Indexes](../../scalability-resilience/search-inverted-indexes.md)'s job), and serving (answering a query against whatever index snapshot currently exists). A query, at request time, becomes a fan-out to every shard of the index that might hold a matching term, a per-shard ranked result set, and a merge of those partial results into one final ranked list — all within the 200ms budget.

## API Surface

- `GET /search?q={query}&page={n}` -> `{ results: [{url, title, snippet, score}], totalEstimate }`
- Internal: `POST /index/documents` (indexer -> shard, batch document upserts), `POST /crawl/seed` (add URLs to the crawl frontier).

## High-Level Design

**Crawler tier** — cross-ref [Web Crawler](../web-crawler/README.md) directly for the frontier/politeness/dedup mechanism; not re-derived here.

**Indexer** — consumes crawled pages, tokenizes, and builds inverted-index postings (cross-ref [Search & Inverted Indexes](../../scalability-resilience/search-inverted-indexes.md)) — asynchronously, off the query-serving path entirely, via a queue ([Message Queues & Pub/Sub](../../hld-building-blocks/message-queues-pubsub.md)) so indexing throughput and query-serving throughput scale independently.

**Ranking** — two signals combined: a query-independent authority score (a PageRank-style signal computed offline, in a periodic batch job over the link graph, since it barely changes page to page) and a query-dependent relevance score (TF-IDF or BM25 against the matched terms, cross-ref [Search & Inverted Indexes](../../scalability-resilience/search-inverted-indexes.md)'s mention of both by name). The offline signal is precomputed and stored WITH each document's postings, so the query path never computes it live.

**Index sharding** — the 20TB index is partitioned across shards by **term hash** (not by document): every posting list for a given term lives entirely on one shard. This means a single-term query only needs to fan out to the one shard owning that term, but a multi-term query needs results from every shard owning any of its terms, then an intersection/merge — the trade-off named explicitly in [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md): the shard key is chosen to match the dominant query pattern (multi-term queries against a term-partitioned index), accepting fan-out cost on the queries that don't fit it perfectly.

**Query-result caching** — identical or near-identical queries recur constantly (cross-ref [Caching Strategies](../../hld-building-blocks/caching-strategies.md)); a cache in front of the ranking/merge step absorbs a large fraction of the 300,000/sec peak before it ever reaches a shard.

**Load Handling.** The dominant cost under load is the shard fan-out multiplier, not the raw query count — a single popular multi-term query touching 20 shards at 300,000 peak queries/sec is 6M shard operations/sec from that one query pattern alone. Query-result caching absorbs repeat queries before fan-out happens at all; beyond that, [Backpressure, Load Shedding & Bulkheads](../../scalability-resilience/backpressure-load-shedding.md) applies directly — a slow or overloaded shard gets a bounded per-shard timeout, and the merge step returns a ranked list built from whichever shards answered in time rather than blocking the whole query on the single slowest shard (a partial-but-fast result beats a complete-but-late one against a 200ms budget). Load-test target: sustain 300,000 queries/sec with p99 under 200ms for 10 minutes, including a simulated single-shard slowdown.

**Concurrent-User Handling.** The read path (queries) and write path (index updates) touch the same shards concurrently, and they don't need to serialize against each other: a shard's index segment is updated by appending a new immutable segment and only atomically swapping which segment set is "current" for queries once the new segment is fully built — a query reading mid-build never sees a half-written segment, and no query-side lock is needed at all, since the swap itself is the only synchronization point (cross-ref [Distributed Locks](../../scalability-resilience/distributed-locks.md) for why a lock held on every read would be the wrong tool here: reads vastly outnumber writes, so the design puts the coordination cost entirely on the rare write side).

## Low-Level Design

**Scatter-gather query execution**, pseudocode:
```
Coordinator.search(query):
    terms = tokenize(query)
    shard_ids = { shardFor(term) for term in terms }   # term-hash routing

    partial_results = []
    for shard_id in shard_ids (in parallel, each with a bounded timeout):
        partial_results.append(Shard[shard_id].searchLocal(terms))
        # a shard that times out contributes nothing; it does not block the others

    merged = mergeAndRank(partial_results)   # k-way merge on score, since each
                                               # partial result is already locally sorted
    return merged.top(pageSize)
```
The merge is a k-way merge, not a full re-sort: each shard already returns its own locally-ranked top results, so combining N already-sorted lists into one final ranked list is `O(total results x log N)`, not `O(total results log total results)`.

**Indexer segment build**, briefly: new documents accumulate into an in-memory buffer; once it reaches a size threshold, it's flushed as a new immutable on-disk segment, and the shard's "current segment set" pointer is swapped to include it — the same immutable-segment-plus-atomic-swap pattern named above, which is what lets indexing and querying proceed concurrently without a shared lock.

## Database Design & Scaling

- **Inverted index (per shard):** `term -> [(doc_id, positions, offline_score)]` postings, term-hash-sharded as described above.
- **Document metadata store:** `doc_id -> {url, title, crawl_timestamp, snippet_source}` — separate from the postings themselves, since metadata is looked up once per RESULT (a handful of rows) while postings are scanned per MATCHING document (potentially millions) — different access pattern, same reasoning [`ecommerce-schema-worked-example.md`](../../database-design/ecommerce-schema-worked-example.md) uses to split `inventory` from `products`.
- **Crawl frontier and dedup store:** owned entirely by the crawler tier, cross-ref [Web Crawler](../web-crawler/README.md) — not part of the serving path at all.
- **Replication:** each shard is replicated (cross-ref [Database Replication & Failover](../../database-design/db-replication-failover.md)) so a single node failure doesn't take an entire term-range out of the index; the coordinator routes a shard's query to any healthy replica.

## Interviewer Q&A

**What happens when two requests hit the same resource at the same instant?**
The read path (queries) and the write path (a segment build finishing and swapping in) touch the same shard concurrently by design, not by accident: the swap from old segment set to new is a single atomic pointer update, so an in-flight query either sees the old, fully-consistent segment set or the new one — never a partially-written one. No query ever blocks waiting for an indexing operation, and no indexing operation waits for a query.

**What happens when traffic spikes 10x for an hour?**
Query-result caching absorbs the largest share of a spike immediately, since a traffic spike is usually concentrated on a smaller-than-usual set of trending queries, not spread evenly. For what isn't cached, per-shard bounded timeouts and partial-result merging (from the Load Handling section) mean the system degrades toward "slightly less complete results, still within budget" rather than an outright timeout cascade; the coordinator tier itself scales horizontally, since it holds no state between requests.

**Term-partitioned vs. document-partitioned index — why term, and what would change with the other choice?**
Document-partitioning (each shard holds complete postings for a subset of documents) makes a single-shard query self-contained but means EVERY query has to fan out to EVERY shard, since any shard might hold a matching document. Term-partitioning only fans out to the shards actually holding the query's terms — a real win for the common multi-term-but-not-all-terms-everywhere case, at the cost of needing a merge/intersection step this design already has to do anyway.

**How would you handle a query with a typo or near-miss term?**
Layer a fuzzy-match pass on top rather than baking it into the core index: check an edit-distance-tolerant structure for the mistyped term, suggest/substitute the closest real indexed term, and re-run the same scatter-gather path — the same "layer it on top, don't rebuild the core structure for it" answer [Search Autocomplete](../search-autocomplete/README.md) gives for the identical question about its trie.

**How do you keep the offline authority score (PageRank-style) from going stale as the link graph changes?**
Recompute it in a periodic batch job (hours to days, not real-time) over the current link graph and republish it into the postings the next time each shard's segments rebuild — the same "small, deliberate staleness window" trade-off this guide's [Search & Inverted Indexes](../../scalability-resilience/search-inverted-indexes.md) page already names for keeping a search index in sync with a primary store, applied here to a score instead of a document set.

**Why not just rank purely by the offline authority score and skip per-query relevance scoring?**
Because authority answers "how generally important is this page," not "how well does this page answer THIS query" — a page can be broadly authoritative and still irrelevant to a specific query's terms. Combining both signals is what lets a highly relevant but less-famous page outrank a famous-but-off-topic one.

**How would you serve a query if the index segment currently being read is mid-swap?**
It can't be, by construction: the swap is a single atomic pointer update to "current segment set," so there's no window where a reader observes a torn or half-updated set — this is exactly what the segment-swap design in Low-Level Design is for.

**Would you shard the document metadata store the same way as the postings (by term)?**
No — metadata is looked up by `doc_id`, not by term, so sharding it by `doc_id` (or replicating it broadly, since it's much smaller than the postings) matches its own actual access pattern, the same "the shard key should match the dominant query, not be copied from an unrelated table's key" reasoning this guide's sharding page makes generally.
