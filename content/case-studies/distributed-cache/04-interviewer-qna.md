# Module 04 — Interviewer Q&A

1. **What happens when a cache node dies?**
It drops off the ring (health check timeout), and the ring's ordinary clockwise rule reassigns its slice to the next live node. That node never held those keys, so every one of them is a guaranteed miss until naturally repopulated from the database — only that slice is affected, per Module 01's Load Handling; every other node's keys are untouched.

2. **How do you stop a hot key's TTL expiry from spiking the database?**
The fetching marker (Module 02) — the first miss claims it and queries the database once; every other concurrent miss for that same key waits on the marker instead of independently repeating the query. Without this, N concurrent readers of one expired key would produce N simultaneous identical database queries, exactly the stampede this design is built to prevent.

3. **Why doesn't this design replicate every node the way the Distributed Key-Value Store does?**
Because the two systems solve different problems — cross-ref [Designing a Distributed Key-Value Store](../distributed-key-value-store/01-architecture-hld.md): that design is a system of record where losing a node must never lose data, so replication is mandatory. A cache never holds the only copy of anything; a cold-start miss against the database is already cheap, so paying to replicate every node by default would be protecting against an outcome that mostly self-heals in one query.

4. **What's the actual cost of skipping replication?**
A slower response for keys on a dead node's slice until they're repopulated — not lost data, not an outage. Module 01's Load Handling sizes the cluster with enough headroom that even a full node's slice landing on its neighbor doesn't trigger a second-order eviction wave on that neighbor's own previously-warm keys.

5. **How would you decide the TTL for a given key?**
A function of how stale the value can tolerably be and how expensive a miss is to resolve. A cheap-to-refetch, fast-changing value can take a short TTL since misses are free; an expensive-to-recompute, slow-changing value wants a longer TTL, since every expiry is a real cost against the database, not just a formality — cross-ref [Caching Strategies](../../hld-building-blocks/caching-strategies.md).

6. **Two concurrent `set()` calls land on the same key at nearly the same instant — what happens?**
They're just two ordinary writes to the same key on the one node that owns it; the node's local store serializes them the same way any single-node in-memory store serializes concurrent writes, and whichever lands last wins. No distributed conflict resolution is needed, because a key only ever lives on one node at a time — unlike the [Distributed Key-Value Store](../distributed-key-value-store/02-lld.md), where the same race needs a vector clock because the key exists on N replicas simultaneously.

7. **Why is the `fetching` marker a field on the node's own record instead of a separate distributed lock service?**
Because the question it answers — "has anyone already claimed this key's refill" — only ever needs an answer scoped to one key, on the one node that owns it. A distributed lock service would add a network hop and a new failure mode to a check a single in-memory compare-and-set already answers atomically for free.

8. **What if the loader (the database query behind a miss) itself times out or fails?**
The marker is cleared in a `finally` block specifically so a failed fetch never leaves it stuck — a stuck marker would make every waiting reader wait forever for a refill that already gave up. A waiter with its own bounded timeout falls through to a direct database read rather than waiting indefinitely, preserving the "a miss is always survivable" guarantee even when the fetch itself fails.

9. **Would you ever add replication back in for this design?**
Yes, as a targeted escalation, not a redesign — Module 01 names two cases: a hot key whose cold moment produces enough coalesced-fetch load that even one database round trip is too much, or a key whose miss is itself expensive to recompute (a heavy aggregation, a rate-limited external call). Replicating just those specific keys to a standby lets a failover promote an already-warm copy instead of a cold node rebuilding it from scratch.

10. **How is this design's consistency model different from the key-value store's, and why does that difference matter for how each is used?**
This design offers no consistency guarantee across the cache and the database at all — a value can be stale for up to its TTL, an explicit accepted trade, not a tunable dial. The [Distributed Key-Value Store](../distributed-key-value-store/00-overview.md) instead makes consistency a per-operation choice (`W`/`R` quorum) because it *is* the system of record and has to offer a real guarantee. That's exactly why this design is the right choice for a lookup that's allowed to be briefly wrong, and the wrong choice for anything that isn't — the same distinction [Consistency Models](../../hld-building-blocks/consistency-models.md) draws in general.
