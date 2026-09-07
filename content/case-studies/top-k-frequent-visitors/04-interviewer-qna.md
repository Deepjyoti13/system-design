# Module 04 — Interviewer Q&A

**1. Why not just run `SELECT visitor_id, COUNT(*) FROM logs GROUP BY visitor_id ORDER BY COUNT(*) DESC LIMIT K` if you're willing to throw enough hardware at it?**
Because the `GROUP BY` has to materialize one running counter per *distinct* visitor ID before it can sort anything — with hundreds of millions of distinct IDs, that intermediate state (Module 00's ~10GB estimate) is the actual bottleneck, not the final K-row answer. The Count-Min Sketch sidesteps this by never holding a per-ID counter at all.

**2. How would you size `d` and `w`?**
From the target error tolerance and confidence, not by guessing: `w = e/ε` and `d = ln(1/δ)`, the same sizing math [Bloom Filters](../../scalability-resilience/bloom-filters.md) walks through for its own false-positive-rate trade-off. Module 00 works this out concretely for ε=0.1%, δ=99%: `d=5`, `w≈2,719`, a ~54KB sketch regardless of log size.

**3. Why does taking the MINIMUM across the `d` rows work, and not, say, the average?**
Every counter can only ever be inflated by unrelated collisions, never deflated below the truth — every real increment for an item hits every one of its `d` cells. The minimum picks whichever row happened to collect the least collision noise for that item; an average would drag the estimate upward by folding in every row's noise instead of discarding the least-noisy one.

**4. Would you use this design for an exact top-10 leaderboard a player directly compares their own score against?**
No. [Real-Time Leaderboard](../real-time-leaderboard/00-overview.md) needs exact counting because a user will notice and object to their own score being off — a Count-Min Sketch is the right tool exactly when nobody is checking one specific visitor's exact count, which is true here and false there.

**5. How does the streaming variant keep "most frequent" meaningful instead of accumulating forever?**
A decay/windowing job periodically halves every counter, or partition workers keep per-time-bucket sketches and only sum the recent ones — either way, this is added on top of the same increment/estimate mechanism, which doesn't change at all between the batch and streaming variants (Module 01's Building Blocks table).

**6. What happens to accuracy as you split the log across more and more partitions?**
Each partition's sketch stays the same fixed size (`d × w`) no matter how many partitions there are, so merging doesn't cost more memory as partition count grows. What *does* grow with partition count is the recall risk from question 7 below — more partitions means more chances for a genuinely frequent visitor's events to be spread thin enough that they miss every single partition's local top-K cutoff.

**7. A visitor ranks just below the cutoff in every single partition's local top-K, but is a genuine top-K visitor globally once summed — how does this design avoid losing them?**
`PartitionProcessor` tracks an oversampled local candidate set — `C × K`, not exactly `K` — precisely because a candidate who never gets recorded in any partition's local snapshot can't be re-scored at merge time no matter how good the merge step is (Module 02's error-cases section). This reduces the risk; it doesn't eliminate it, and `C` is a real, named tuning knob against a real, bounded risk.

**8. Why merge the partition sketches instead of just re-scanning the whole log once, on one machine, with a single sketch?**
Because the merge itself is cheap and fixed-cost — `O(d×w)` per partition, completely independent of the original log's size — while re-scanning the whole log again on one machine defeats the entire reason the log was partitioned in the first place. Sketch merging is only this cheap because a sketch is nothing but a counter array; merging exact per-ID hash maps would need a full shuffle keyed by visitor ID instead.

**9. Why can a Count-Min Sketch's error only ever be an overestimate, never an underestimate — the same one-directional guarantee shape as a Bloom filter, mirrored?**
A Bloom filter's bits only ever get set by real inserts, so a query for a real member can never see a false "not present" — no false negatives. A Count-Min Sketch's counters only ever get incremented by real events, so an estimate can never fall below the true count — the mirror-image guarantee, overestimate-only instead of no-false-negatives, for the same underlying reason: [Bloom Filters](../../scalability-resilience/bloom-filters.md) covers exactly this asymmetry for its own structure.

**10. What's the actual concurrency story inside one partition worker — do the sketch and heap need locks?**
Not if a single thread owns a partition's whole scan — there's only ever one writer, so there's no race to guard against in the first place, which is a stronger and simpler guarantee than "the database enforces atomicity for us" elsewhere in this guide. If a partition is itself parallelized across threads for more throughput, each sketch-cell increment needs to become a lightweight in-memory atomic operation, and the heap is best kept per-thread and merged at the end — the identical "local state, merge once" shape recursively applied at a finer grain (Module 02's concurrency section).
