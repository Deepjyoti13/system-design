# Module 04 — Interviewer Q&A

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
