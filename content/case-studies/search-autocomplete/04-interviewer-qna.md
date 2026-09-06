# Module 04 — Interviewer Q&A

**1. Why not just index `search_terms` in a normal relational database and query it with `LIKE 'prefix%'` on every keystroke?**
At the ~46,300/sec peak from Capacity Estimation, a B-tree index supports exact-match or fixed-length range lookups efficiently, not an arbitrary, growing prefix scan re-run on every character — and thousands of users typing the same prefix a second apart would all re-derive the identical answer from scratch, because a relational index remembers nothing between queries. An in-memory trie with a cached top-K per node answers in O(prefix length) and reuses the same precomputed answer for everyone.

**2. How do you keep rankings fresh without making every search a write against the structure serving reads?**
Search events flow through a queue into an append-only log, and a separate batch job (the Frequency Aggregator) periodically recomputes term frequency and rebuilds the trie off to the side. The live, request-serving trie only ever receives a completed, whole replacement generation — never an in-place counter update per search — which is exactly what keeps read-time performance decoupled from write volume.

**3. How would you shard this if the vocabulary is too large for one machine's memory?**
By prefix range — one shard owns `a`-`m`, another `n`-`z`, and so on — because a query's leading character alone determines which shard can answer it, with no fan-out for the common case. A hash-based shard key would scatter every term starting with the same letters across every shard, forcing every query to fan out to all of them just to be sure — the shard key has to match the access pattern that actually runs, the same reasoning [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) applies generally.

**4. What happens to an in-flight request when a snapshot swap happens underneath it?**
Nothing breaks, because the swap is a single atomic reference reassignment, and `suggest()` reads `currentTrie` exactly once at the top of the call into a local variable. A request that started before the swap simply finishes its walk against the old, fully-formed generation; a request that starts after gets the new one from the very first step. Neither ever observes a half-updated node, because no node is ever mutated in place — see [LLD](02-lld.md).

**5. How would you personalize suggestions per user without maintaining a separate trie per user?**
Keep one shared, popularity-ranked global trie as the base layer, and blend in a small, cheap per-user signal at query time (recent searches, boosted or prepended) via a swappable `PersonalizationBlender` rather than duplicating the entire structure per user — a full personal trie per user doesn't scale storage-wise and mostly isn't needed, since the global ranking already covers the overwhelming majority of completions.

**6. What's the first thing you'd shed if this system were overloaded?**
Personalization Blend, always — it's an enrichment layer on top of an already-correct base answer, so skipping it degrades personalization only, never correctness or availability. The base cached top-K lookup is never shed; if the system truly can't serve it, it fails the request outright rather than quietly returning a degraded or wrong answer dressed up as a real one.

**7. How do you handle typos or near-matches, which a pure prefix trie doesn't naturally support?**
A pure trie only helps with exact-prefix matches. Fuzzy/typo tolerance is deliberately layered on as a separate pass — edit-distance matching against nearby candidates, or falling back to a full-text search index for a "did you mean" — rather than baked into the trie itself, which stays simple and fast for the overwhelmingly common exact-prefix case.

**8. Why is `search_events` append-only instead of updating a running frequency counter directly?**
An in-place counter update makes the single most popular term a write hotspot — every occurrence of that term serializes against the same row, at exactly the volume where contention would hurt most. Appending and aggregating later in a batch groupby turns per-event write contention into a periodic, parallelizable scan instead.

**9. Why store the trie snapshot in blob storage instead of a database?**
A full snapshot is a large, single, query-pattern-free object — fetched whole by one version key, never partially queried — the same shape this guide's [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md) argues belongs outside a primary database. A database would add transactional overhead this workflow doesn't need, since replicas only ever need to pull one complete file and swap a pointer.

**10. How would you test the frequency-rebuild pipeline without waiting for a real multi-hour batch cycle?**
Run `FrequencyAggregator.rebuild()` against a small fixture log with a fixed, known checkpoint window, then assert the resulting `cachedTopK` on specific nodes matches hand-computed expectations — a contract test against the `TermFrequencyRepository` and `TrieBuilder` interfaces, independent of production log volume or a real clock, the same interface-over-implementation testing approach this guide uses for its other pluggable dependencies.
