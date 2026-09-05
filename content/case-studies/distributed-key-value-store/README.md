# Design a Distributed Key-Value Store

![A key's N=3 replicas on the hash ring, with a W=2 write and an overlapping R=2 read](diagrams/hld.svg)

## Requirements

Functional: `put(key, value)`, `get(key)`, `delete(key)`. No queries, no joins, no schema — a value is an opaque blob, addressed only by its key.

Non-functional, stated as assumptions: tens of millions of keys, a few hundred thousand ops/sec at peak, and the one requirement that shapes everything below — **no single node failure should lose data or block the whole store**, with consistency itself left *tunable* rather than fixed. This is the Dynamo-style design: prioritize availability and horizontal scale over the strong guarantees a relational store would default to (cross-ref [SQL vs NoSQL](../../database-design/sql-vs-nosql.md) — this is the query-pattern-driven case for the "vs NoSQL" side).

## Where the abstractions stop being abstract

This case study is the concrete system [Consistent Hashing](../../hld-building-blocks/consistent-hashing.md) and [Consistency Models](../../hld-building-blocks/consistency-models.md) were building toward. Their ring and their `W`/`R`/`N` quorum math aren't background theory here — they *are* this system's data-placement and read/write mechanism.

Every key hashes onto the ring. A key's value isn't stored on one node — it's replicated to the **N consecutive nodes clockwise** from the key's ring position (consistent hashing's own "preference list" answer to a down owner, applied deliberately instead of as a fallback). N specifically, not 1, because it's what lets the store survive up to `N-1` node failures without losing the key at all.

## Reads and writes, using the quorum mechanism

A write succeeds once `W` of the `N` replicas acknowledge it. A read queries `R` replicas and returns the most recent value among them. [Consistency Models](../../hld-building-blocks/consistency-models.md) already worked the concrete numbers: `N=3, W=2, R=2` guarantees every read set overlaps every write set in at least one replica, by the pigeonhole principle (`2+2-3=1`). That's not an abstract property here — it's the actual reason a `get()` right after a `put()` returns the value that was just written, even though the read almost certainly hit a *different pair* of replicas than the write did.

"Most recent" needs a way to compare two versions of the same key, and there are two real choices:

- **Last-write-wins (LWW)**, using a timestamp — simple, cheap, and the usual default. Its failure mode: two concurrent writes to the same key on two different replicas (a partition, or just two clients racing) resolve by silently keeping one and discarding the other, with no signal that a write was ever lost.
- **Version vectors** — each value carries a per-replica counter, so the system can *detect* that two versions are concurrent (neither happened-before the other) rather than guessing. It doesn't resolve the conflict for you; it hands both versions back and pushes resolution to the client or application, which at least means data isn't silently dropped.

## Node failure and partitions, concretely

Two mechanisms from [Consistency Models](../../hld-building-blocks/consistency-models.md) keep the replica set honest between quorum operations:

- **Hinted handoff** — if one of a key's N replicas is down when a write arrives, another node in the write path holds the write temporarily and delivers it once the original recovers, instead of the write just failing.
- **Read repair** — if a read notices its queried replicas disagree, it patches the stale one immediately, using the read path itself to heal drift.

`W` and `R` are the tuning knob, per operation: turn both up and the system moves toward strong consistency at the cost of write/read *availability* during a partition — a write or read can outright fail if too few replicas are reachable ([CAP](../../foundations/latency-throughput-cap.md)'s CP side). Turn them down (`W=1, R=1` is the extreme) and the system stays available through almost anything, at the cost of a read possibly missing the latest write.

## Interviewer follow-ups

**What happens if two clients write to the same key concurrently on two different replicas during a partition, and neither knows about the other's write?**
Under LWW, whichever timestamp is later wins and the other write is gone with no trace. Under version vectors, both versions survive as siblings and get returned together on the next read — the system admits it can't pick a winner and leaves that decision to whoever reads it next.

**How would you handle a value too large to fit comfortably in memory on one node?**
Store the value itself in a blob store keyed by content hash ([Object / Blob Storage](../../scalability-resilience/object-blob-storage.md)) and put only a small pointer through the key-value store's normal replication path — replicating a multi-megabyte blob to N nodes on every write is exactly the kind of large-object cost this guide's object-storage page already argues against paying inside a primary data path.

**Why might you choose W=1 for a write-heavy, loss-tolerant workload like clickstream events, versus W=N for something that can't tolerate any loss?**
`W=1` accepts a write the instant *any* replica has it — fastest possible writes, and losing one event out of a high-volume stream is invisible in aggregate. `W=N` waits for every replica before acknowledging, trading that speed for a guarantee that the value is never gone as long as even one node used at write time is still alive — worth it only when a single lost write is itself the failure, not just a slower one.
