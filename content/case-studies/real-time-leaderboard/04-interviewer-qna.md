# Module 04 — Interviewer Q&A

**1. What happens when two requests hit the same resource at the same instant?**
Two concurrent score updates for the SAME user are naturally serialized by the sorted-set store's own atomic increment command — no external lock needed. The actual hazard is one logical update being submitted twice (a client retry), which the `match_id`-based idempotency key catches before the increment ever runs twice.

**2. What happens when traffic spikes 10x for an hour?**
This system's real spike is read-heavy, not write-heavy — a tournament finale means everyone refreshing the same handful of leaderboards. A short-TTL (1-2s) cache of each leaderboard's top-K in front of the sorted-set store absorbs the fan-in; a few seconds of staleness on a leaderboard nobody expects sub-second accuracy from is an acceptable trade, not a correctness bug.

**3. Why a sorted-set structure instead of `SELECT ... ORDER BY score DESC LIMIT 100` against a regular database?**
That query is O(N log N) to sort or relies on an index scan that still costs more than a purpose-built ordered structure with O(log N) inserts and O(log N + K) range reads — and computing a SPECIFIC user's rank ("what position is user X in") is a full scan-and-count in a plain relational table, versus a single O(log N) operation in a sorted set.

**4. Why keep a separate durable event log if the sorted-set store already replicates for HA?**
Replication protects against a single node failing, not against losing the ENTIRE replica set, a bad deploy that corrupts the in-memory structure, or needing to seed a brand-new region. The event log is the only copy that's independent of the in-memory structure's own failure modes — replication and the event log protect against different classes of loss.

**5. How would you reset a leaderboard for a new season without any downtime?**
Write to a NEW sorted-set key (`leaderboard:season8`) from the moment the new season starts, while `leaderboard:season7` keeps serving reads until its snapshot is taken — the reset is "start using a new key," not "clear the old one in place," so there's no window where reads race a clear operation.

**6. How would this scale to a leaderboard with a billion members, where even top-K needs to stay fast?**
Beyond a certain size, "exact rank for every user" stops being worth its cost — shard by `user_id` hash into many sub-leaderboards, keep exact top-K per shard, and for a given user's OWN rank, approximate it (sample-based percentile estimate) rather than computing an exact global rank across all shards on every request.

**7. Why is the idempotency key `match_id:user_id` rather than just `match_id`?**
A single match can produce score updates for multiple users (e.g. both players in a head-to-head match) — keying on `match_id` alone would let the SECOND player's legitimate update collide with the dedupe record from the first, silently dropping it.

**8. How would you support "nearby" (rank ± 5) without 11 separate rank lookups?**
Get the user's own rank once, then issue a single range read spanning `[rank-5, rank+5]` against the same sorted-set structure — one O(log N + 11) operation instead of 11 independent ones.

**9. What if the dedupe record expires (24h TTL) before a very late retry arrives — does that risk a double-count?**
Yes, and this is a named, accepted trade rather than a hidden gap: a retry arriving more than 24 hours after the original is vanishingly unlikely for a synchronous match-result flow, so the TTL is chosen to comfortably outlast any realistic retry window while still bounding the dedupe store's memory footprint — an unbounded dedupe TTL would mean storing one record per score update forever, defeating the reason it's a separate lightweight store instead of the durable event log.

**10. Would you ever recompute the entire leaderboard from the event log on a normal read path, instead of trusting the sorted-set store?**
No — the event log's ordered replay is deliberately reserved for the crash-recovery path alone; replaying potentially billions of historical events to answer one rank query would be strictly slower than the O(log N) the sorted-set store already gives you for free. The event log's job is durability across total failure, not being a queryable source of truth for everyday reads.
