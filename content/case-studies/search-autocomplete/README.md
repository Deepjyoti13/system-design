# Design Search Autocomplete

![A prefix trie for cat/car/cart: shared paths, cached top-K completions per node, and a query walk](diagrams/hld.svg)

## Requirements

As a user types each character, return the top-K most likely completions, ranked by popularity/frequency — not just any match, the ones people actually search for.

Non-functional: suggestions have to appear within roughly **100ms of each keystroke**. That number matters more than it looks — this endpoint fires on every single character typed, not once on submit. A search box that lags by even a few hundred ms per keystroke feels broken in a way a slow full-page-search result never does.

## Why a database query per keystroke doesn't work

The naive version — `SELECT query FROM search_terms WHERE query LIKE 'sal%' ORDER BY frequency DESC LIMIT 10` — run live against a table, on every keystroke, from every typing user, breaks two ways at once. It's slow: a B-tree ([Database Indexing](../../database-design/database-indexing.md)) supports an exact-match or a fixed-length range efficiently, not an arbitrary-and-growing prefix scan re-run on every keystroke. And it's wasteful: thousands of different users typing "sal" a second apart all re-derive the identical top-10 answer from scratch, every time, because nothing remembers the answer between requests.

## A genuinely different problem from full-text search

[Search & Inverted Indexes](../../scalability-resilience/search-inverted-indexes.md) solves "which documents contain this exact word" — a whole-token containment problem, served by a word -> document-list mapping. Autocomplete asks something else: "which known queries *start with* these few characters" — a prefix-match problem. An inverted index's keys are complete words; it has no notion of "everything starting with these three letters" as a single lookup. That's not a smaller version of the same problem, it needs its own structure.

## The trie (prefix tree), precisely

A trie represents each **character** as a node; a path from the root spells out a prefix. Insert `"cat"`, `"car"`, and `"cart"`:

- Root -> `c` -> `a` — both words share this path, so it exists once, not twice.
- At `a`, the path branches: `t` completes `"cat"`; `r` continues toward `"car"` and `"cart"`.
- At `r`, another branch: this node itself marks the end of `"car"`, *and* continues to `t` for `"cart"`.

Once you've walked the 2 characters of a typed prefix down to its node, every valid completion is just whatever's reachable in the subtree below — nothing outside that branch is ever touched, no matter how large the trie gets elsewhere. That's the entire speed argument: a lookup costs O(prefix length), not O(number of things that could match).

The practical optimization that makes it fast in production, not just in theory: **each trie node caches its own precomputed top-K completions** (say, the 10 most popular full terms reachable below it), refreshed periodically rather than recalculated on the spot. A query then isn't "walk the subtree and rank everything you find" — it's "walk to this node, return the list already sitting there." Same O(prefix length) cost, but now with a constant, tiny amount of work at the end instead of a subtree scan.

## Keeping frequencies fresh without making search volume a write problem

If updating a node's cached top-K happened synchronously on every search, search traffic itself would become write load on the exact structure serving read traffic — the opposite of what a caching layer is for. Real systems don't do that: search events flow through a queue ([Message Queues & Pub/Sub](../../hld-building-blocks/message-queues-pubsub.md)) into a log, and a **batch job** periodically (every few minutes to every few hours, depending on how fast trends need to move) recomputes each node's top-K from the recent log and republishes an updated trie or patches it in place. The live-serving trie is a few minutes stale on term popularity at any given moment — a small, deliberate lag, not a bug ([Logs, Metrics & Distributed Tracing](../../scalability-resilience/logs-metrics-tracing.md) makes the same point about not needing every signal instantly consistent).

## Interviewer follow-ups

**How would you personalize suggestions per user without maintaining a separate trie per user?**
Keep one shared, popularity-ranked global trie as the base layer, and blend in a small, cheap per-user signal on top at query time (the user's own recent searches, boosted or prepended) rather than duplicating the entire structure per user — a full personal trie per user doesn't scale storage-wise and mostly isn't needed, since the global ranking already covers the common case.

**How would this trie be sharded if it's too large for one machine's memory?**
By prefix range — e.g. one shard owns everything starting `a`-`m`, another `n`-`z` — since a query's first character alone determines which shard can answer it, so there's no fan-out needed for the common case. This is the same reasoning [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) uses for choosing a shard key that matches the actual query pattern.

**How would you handle typos or near-matches, which a pure prefix trie doesn't naturally support?**
A pure trie only helps with exact-prefix matches; fuzzy/typo tolerance is usually layered on as a separate pass (edit-distance matching against the top candidates, or falling back to the full-text search index for a "did you mean" suggestion when the trie returns nothing) rather than baked into the trie itself.
