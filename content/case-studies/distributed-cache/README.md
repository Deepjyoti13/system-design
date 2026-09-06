# Design a Distributed Cache

![A cache node failing: only its ring slice falls through to the database, the rest of the ring keeps serving](diagrams/hld.svg)

## Requirements

**Functional:** `get(key)`, `set(key, value, ttl)`, `delete(key)` — served to many independent client applications sharing one cache tier, not one app's private in-process cache. **Non-functional:** millions of distinct keys, sub-millisecond p99 read latency, and the requirement that actually shapes this design — a single node dying must never take the cache tier down or cascade load into the database. Losing that node's keys is fine; a miss just falls through and gets refetched. Staying up and staying fast is the actual bar, not durability.

## Placing keys across nodes

This is [Consistent Hashing](../../hld-building-blocks/consistent-hashing.md) applied directly, not re-derived: each cache node claims a slice of the ring, and a router — a client-side library every application links against, or a shared proxy in front of the cluster — hashes the key and walks clockwise to the node that owns it. No coordinator and no lookup service consulted per request; the ring position *is* the routing table. That's exactly why this stays sub-millisecond at millions of keys, and why the fleet can grow without a global reshuffle — adding a node only steals one slice, per the ring's own join behavior.

## What actually happens when a node dies

A cache node's health is tracked (heartbeats, or the router simply timing out on it), and once it's marked down it drops off the ring. The ring's rule doesn't change: a key belongs to the first server clockwise from its position, and for every key that used to belong to the dead node, that's now simply the *next* live node over. Nothing new had to be invented for failure — it's the same mechanism [Consistent Hashing](../../hld-building-blocks/consistent-hashing.md) already uses for a node joining, run in reverse.

The catch is that the next node never cached those keys — they weren't its slice a moment ago. So the first request for each of them is a miss, falls through to the database, and gets written back into the cache from there. Only the dead node's slice is affected; every other key, on every other node, was never touched.

This is worth being precise about, because it's *not* the same failure a real datastore has. A database node dying can mean data that existed nowhere else is gone forever — which is why [replication and consensus](../../hld-building-blocks/replication-consensus.md) exist, to make sure a write survives the one node that first accepted it. A cache never holds the only copy of anything; the database is still the system of record underneath it. So "the node's keys are gone" here means gone from the cache, not gone — they're one query away, and the system self-heals one miss at a time. Calling that "data loss" would be describing a rebuildable optimization as if it were a durability guarantee it never made.

## The thundering herd this graceful-miss behavior can trigger

[Caching Strategies](../../hld-building-blocks/caching-strategies.md) already covers the general version of this: a hot key disappearing — by node failure here, or just a TTL expiry — means every concurrent reader misses at the same instant and all of them hit the database for the same row simultaneously, a spike the DB wasn't sized for.

A shared distributed-cache *service* has a specific twist on the fix. The generic answer, request coalescing, usually means one process holding an in-memory lock so only one of its own threads fetches while the rest wait — but here the concurrent requests for that key aren't threads in one process, they're many separate application hosts talking to the same cache tier. An in-process lock doesn't see any of them. The coalescing signal has to be visible cluster-wide instead: the first miss writes a short-lived "fetching" marker for that key (in the cache itself, or tracked by the proxy layer), goes to the database, populates the real value, and clears the marker. Every other concurrent miss for that same key sees the marker and waits or briefly retries against the cache, instead of each independently issuing its own database query. That's what keeps one popular key's cold moment from turning into a database incident.

## Replication: an escalation, not the default

For most keys, "a dead node's slice cold-starts against the database" is cheap enough that nothing more is needed — which is exactly why a cache tier doesn't run the replication and quorum machinery a real datastore does by default. But two situations make that too slow: a hot key whose node dying means *many* requests hit the coalesced fetch at once and even one database round trip is more load than wanted, or a key whose miss is itself expensive to recompute (a heavy aggregation, a rate-limited external call the cache exists specifically to shield). In either case, the fix is to deliberately [replicate](../../hld-building-blocks/replication-consensus.md) that node's data to a standby, so a failover promotes an already-warm copy instead of handing the key to a cold node that has to rebuild it. This is a targeted escalation for specific hot or expensive keys, not a cluster-wide default — replicating every node roughly doubles the fleet's memory footprint to protect against an outcome that, for most keys, is already free.

## Interviewer follow-ups

**How would you decide what TTL to use for a given key?**
It's a function of two things: how stale the value is allowed to be, and how expensive a miss is. A cheap-to-refetch, fast-changing value (a view counter) can take a short TTL — misses are free, so there's no cost to expiring it often. An expensive-to-recompute, slow-changing value (a heavy aggregate query) wants a long TTL, because every expiry is a real database cost, not just a formality. [Caching Strategies](../../hld-building-blocks/caching-strategies.md) covers the general trade-off; here it's just applied per key rather than picked once for the whole cache.

**How would adding a new cache node affect the existing keys' node assignments?**
Only the slice immediately counter-clockwise from the new node's ring position remaps to it — every other key, on every other node, keeps its existing owner, exactly the join behavior [Consistent Hashing](../../hld-building-blocks/consistent-hashing.md) already works through. The new node starts with an empty cache, though, so that slice is a guaranteed 100% miss rate against the database until it's naturally repopulated — the same cold-start effect a node *failure* produces, just deliberately triggered instead of accidental.

**Does the eviction policy actually matter here, if nodes are already sized to hold their working set?**
Mostly no — day to day, a correctly sized node rarely evicts anything, [Caching Strategies](../../hld-building-blocks/caching-strategies.md)' LRU-vs-LFU choice included. It matters at exactly the moment a node dies: the next node over inherits the dead node's entire slice on top of its own, and if it was only provisioned for its own steady-state working set, it may now have to evict some of its *own* previously-warm keys to make room for the new arrivals. That's a second wave of misses on keys that had nothing to do with the original failure, and it's the eviction policy's job to decide which of its own keys pay that price.

**Why not just replicate every node by default and avoid losing a dead node's keys at all?**
Because the thing being avoided — a handful of keys cold-starting against the database — is already cheap, and replication isn't: it roughly doubles memory cost cluster-wide and adds a consistency question (a stale replica) the cache didn't otherwise have to answer. Paying that permanently, everywhere, to prevent an outcome that self-heals in one query, is a worse trade than paying it selectively for the specific hot or expensive keys where a cold miss genuinely can't be tolerated.
