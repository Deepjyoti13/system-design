# Find the Top K Most Frequent Visitors in a Billion-Row Log

![A Count-Min Sketch approximating frequency counts, feeding a size-K min-heap that tracks the current top-K](diagrams/hld.svg)

## Requirements

Given a log of a billion+ visit events (one row per page visit, with a visitor ID), find the K visitor IDs that appear most often — a heavy-hitters problem, not a lookup problem. Non-functional: the log is too large to sort, and in a streaming variant, the answer needs to stay approximately current as new events keep arriving, without re-scanning everything already processed.

## Why exact counting doesn't scale

The exact answer requires a count per DISTINCT visitor ID — with potentially hundreds of millions of distinct visitors, keeping one counter per ID in memory can itself be the bottleneck, long before you get to ranking them. This is the same shape of problem [Bloom Filters](../../scalability-resilience/bloom-filters.md) solves for set membership — trading a small, bounded chance of error for a huge reduction in memory — applied here to frequency counting instead of membership.

## Count-Min Sketch, precisely, as the frequency-counting equivalent of a Bloom filter

A Count-Min Sketch is a small 2D array of counters (say, `d` rows, each `w` columns wide) with `d` independent hash functions, one per row. To record a visit from visitor `v`: hash `v` with each of the `d` hash functions, and increment the counter at that hashed column in each of the `d` rows. To *estimate* `v`'s count: hash `v` the same `d` ways, look up all `d` counters, and take the **minimum** of them.

The minimum is the whole trick. Different visitor IDs can collide into the same counter in any single row (that row's count for `v` is then inflated by whoever else hashed there too) — but it's very unlikely that the SAME unrelated visitors collide with `v` in every one of the `d` rows simultaneously. Taking the minimum across all `d` rows cancels out most of that collision noise, so the estimate is always at least as high as the true count (never an undercount) and usually very close to it. Exactly like a Bloom filter, this can only ever overestimate, never underestimate — a Count-Min Sketch's error is one-directional, the same guarantee shape in the opposite direction from a Bloom filter's "no false negatives."

## Tracking the top K without re-sorting everything

A Count-Min Sketch answers "roughly how many times has `v` been seen" cheaply, but doesn't by itself answer "who are the top K" — for that, pair it with a **min-heap of size K**: for every incoming event, look up its visitor's estimated count in the sketch, and if that count is higher than the heap's current *smallest* tracked count, evict the smallest and insert the new visitor. The heap never holds more than K entries no matter how many billions of events flow through, and "is this bigger than my current smallest" is a single comparison, not a re-sort.

## Doing this at billion-row scale: partition, then merge

A single machine's memory bounds how large a sketch and heap it can hold comfortably, so the log gets partitioned (cross-ref [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md)) — e.g. by time window or by a hash of the visitor ID — and each partition is processed independently into its own local sketch and local top-K candidates. A final merge step combines the per-partition sketches (Count-Min Sketches merge cell-by-cell by simple addition, since they're just counter arrays) and re-derives the global top-K from the merged sketch and the union of local candidates. This is the same map-then-reduce shape as almost any "answer a global question about data too large for one machine" problem.

## Interviewer follow-ups

**How would you size the sketch (choosing `d` and `w`)?** Both control the estimate's error bound directly: a wider `w` (more columns) reduces how often unrelated IDs collide in any one row; a larger `d` (more rows) gives more independent chances for the minimum to cancel out collision noise. The practical approach is picking `d` and `w` from the target error tolerance and expected event volume, the same kind of sizing math [Bloom Filters](../../scalability-resilience/bloom-filters.md) already walks through for its own false-positive-rate trade-off.

**Would you use this for an exact "top 10 leaderboard" a user directly compares scores on?**
No — a Count-Min Sketch is the right tool when approximate is genuinely acceptable and the win is memory, not when a user will notice and object to their own count being slightly off. [Real-Time Leaderboard](../real-time-leaderboard/README.md), where every user can see and compare their own exact rank, needs exact counting; this heavy-hitters log analysis, where nobody is checking one specific visitor's exact count, doesn't.

**How would this work as a continuous stream instead of a one-time batch job over a fixed log?**
The sketch and heap both update incrementally per event already — nothing about the core mechanism changes for a stream. What has to be added is a decay or windowing strategy (e.g. periodically halving all counters, or keeping sketches per time-bucket and only summing recent buckets) so that "most frequent" reflects recent behavior instead of accumulating forever and making today's spike invisible against a year of history.
