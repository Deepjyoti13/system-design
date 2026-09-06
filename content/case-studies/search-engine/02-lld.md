# Module 02 — Low-Level Design

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
