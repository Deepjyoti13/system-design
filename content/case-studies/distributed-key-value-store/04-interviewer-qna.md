# Module 04 — Interviewer Follow-Up Bank

1. **What happens when two requests hit the same key at the same instant?**
Two concurrent writes to the same key, landing on different replicas before either propagates, are detected as concurrent by the version vector (neither happened-before the other) rather than one silently overwriting the other — both versions are returned together on the next read, and resolving which one "wins" is pushed to the client. Under LWW instead, whichever timestamp is later wins and the loser's write is gone with no signal it ever happened.

2. **What happens when traffic spikes 10x for an hour?**
Both reads and writes scale by adding storage nodes to the ring — a ring join only requires the new node's immediate neighbors to hand off a slice of their data, not a cluster-wide rebalance, so capacity can be added incrementally under live load. If nodes can't be added fast enough, the coordinator's bounded-timeout fan-out still returns clean `InsufficientReplicas` failures rather than silently under-replicating writes.

3. **Why does `W + R > N` guarantee a read sees the latest write?**
By the pigeonhole principle: if a write's `W` acks and a read's `R` responses are both drawn from the same `N` replicas, and `W + R > N`, the two sets can't be fully disjoint — they must share at least one replica, and that shared replica has the latest write.

4. **Is a single-node database CP or AP?**
Neither, really — CAP only describes behavior *during a network partition between nodes*, and a single node has no partition to have a side on. It's simply unavailable if that one node goes down, which is precisely the failure mode this entire multi-replica design exists to avoid.

5. **Why replicate to N consecutive ring nodes instead of N nodes chosen at random each time?**
Consecutive placement means every node can compute a key's replica set locally from the ring alone, with no lookup — a random placement would need a separate, centrally-tracked mapping per key, reintroducing exactly the single point of failure consistent hashing is designed to avoid.

6. **How would you handle a value too large to comfortably replicate on every write?**
Store the actual bytes in a blob store keyed by content hash and replicate only a small pointer through this system's normal write path — replicating a multi-megabyte value to `N` nodes on every write is the large-object cost this guide's object-storage guidance already argues against paying inside a primary data path.

7. **Why might you choose `W=1` for a write-heavy, loss-tolerant workload, versus `W=N` for something that can't tolerate any loss?**
`W=1` acknowledges the instant any single replica has the write — the fastest possible option, and losing one event out of a high-volume stream (like clickstream data) is invisible in aggregate. `W=N` waits for every replica, trading that speed for a guarantee the value is never gone as long as even one node from write time survives — worth it only when a single lost write is itself the failure.

8. **What does turning `W` and `R` both up cost you?**
Both latency (a write/read now waits on more replicas, including the slowest one in the set) and availability during a partition — if fewer than the required number of replicas are reachable, the operation fails outright rather than degrading gracefully, which is CAP's CP side made concrete.

9. **How does a node rejoining after downtime avoid serving stale data immediately?**
Hinted-handoff writes it missed are replayed to it directly once gossip detects it's back, and any read that happens to hit it before that replay completes will disagree with the other replicas queried — which is exactly what read repair is for, patching the stale replica as a side effect of a normal read rather than needing a separate recovery step.

10. **Would you ever use this key-value store as the *only* database in a larger system, like this guide's e-commerce platform?**
Only for the specific slice of data whose access pattern really is pure key lookup with no joins and no need for a "list all X for user Y" query — this guide's [E-Commerce Platform](../ecommerce-platform/01-architecture-hld.md) deliberately keeps its order/checkout core on a relational store for exactly the transactional and relational guarantees this design doesn't provide, and would use something like this only for a narrower job (a session cache, a feature store) alongside it.
