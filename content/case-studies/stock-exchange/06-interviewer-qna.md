# Module 06 — Interviewer Q&A

---

### 1. 215,000 orders/sec doesn't sound hard. Why is this a difficult system?

It isn't a throughput problem, and saying so early is the right move. 215k orders/sec is modest — the [message queue](../distributed-message-queue/00-overview.md) in this guide handles twice that per node, and nobody needs to shard for capacity here. Volume also splits cleanly across 100 independent symbol books, so it's really 100 streams of ~2,150/sec.

**This is a latency design.** The requirement is tens of microseconds at p99.99, and that single number eliminates almost every tool:

| Operation | Cost | Fraction of a 30 µs budget |
|---|---|---|
| Shared-memory IPC | 0.1–1 µs | 0.3–3% |
| TCP round trip, same rack | 50–100 µs | **170–330%** |
| TCP round trip, same DC | ~500 µs | **1,700%** |
| SSD read | 150 µs | **500%** |
| Kafka produce+ack | 1–10 ms | **3,000–33,000%** |

One same-rack network hop consumes the entire budget. So the critical path has no network, no disk, no database, no logging — it's one process on one box using shared memory.

And the second thing that makes it hard: **the tail is the product, not a quality metric.** A system with a 5 µs mean and a 2 ms p99.99 is unusable, because that 2 ms lands on someone's order and costs them money. Most systems in this guide optimize the average and monitor the tail; here it's inverted.

---

### 2. Walk me through the order book data structure.

Four operations with wildly uneven frequencies, and the surprising one drives the design:

| Operation | Required |
|---|---|
| Add a resting order | O(1) |
| Match against the best opposite price | O(1) per fill |
| **Cancel by order id** | **O(1)** |
| Query best bid/ask | O(1) |

**Cancel is the hot path**, because in real markets the large majority of orders are cancelled rather than filled — market makers post and pull quotes continuously. Any structure with O(n) cancel is disqualified no matter how fast its matching is.

That kills the instinctive answer. **A heap** gives O(1) best price and O(log n) insert, but **cancelling an arbitrary order by id is O(n)** — a heap gives you no way to find an element you didn't pop. It also can't express time priority within a price level, since it orders by price alone.

The structure is two-level:

```
OrderBook
  buyBook / sellBook
      levels: Map<Price, PriceLevel>    ← array-indexed by integer price where possible
  orderIndex: Map<OrderId, Order*>      ← THE key to O(1) cancel
PriceLevel
  totalVolume                            ← maintained incrementally; L2 depth is O(1)/level
  head, tail                             ← FIFO doubly-linked list of orders
```

Three decisions do the work:

- **Price level, then FIFO queue within it** — this *is* price-time priority, encoded as the shape of the data rather than as comparator logic, so a bug can't violate it.
- **`orderIndex` maps id → object directly**, and the object holds `prev`/`next`, so unlinking is a constant-time pointer update. No search. This is the heap's fatal flaw solved.
- **Intrusive doubly-linked list** — links inside the `Order`, so one allocation and one cache line. Singly-linked would make cancel O(n) again.

Beyond big-O, the constant factors *are* the design: pre-allocated object pools (never `malloc` on the path), `bestLevel` cached as a pointer, `totalVolume` maintained incrementally, and array-indexed price levels for cache locality instead of tree pointer-chasing.

---

### 3. Two orders that could match arrive at the same time. Who wins, and how do you prove it?

**Price-time priority**: better price wins; at equal price, earlier arrival wins.

The "how do you prove it" half is the real question, and the answer is the **sequencer**. Every inbound order gets a monotonic sequence number from a single writer before it reaches the matching engine. That sequence *is* the arrival order — not an inference from timestamps, an explicit recorded fact.

Why not timestamps: they have resolution limits (two orders in the same nanosecond are indistinguishable) and clock skew between gateways. A monotonic integer from one writer has neither problem, and two orders always have distinct sequence numbers.

The engine then processes events serially in sequence order, so the second order matches against the book *as the first left it*. No lock, no race — the sequencer converted a concurrency problem into an ordering fact.

One detail people get wrong: **the execution price is the resting order's price, not the incoming one's.** A resting sell at $415 meeting a buy willing to pay $420 trades at **$415** — the buyer gets a better price than asked. The resting order established the price and holds time priority; the aggressor takes what's offered. Using the incoming price would overcharge every crossing order.

---

### 4. Why is everything on one server? That's not how you build distributed systems.

Because the arithmetic doesn't permit anything else. A microservice critical path — gateway → order service → risk service → matching service — is four network hops. At 50–100 µs per same-rack round trip that's 200–400 µs, and the budget is tens of microseconds. You'd miss by an order of magnitude before doing any work.

Shared-memory IPC is 0.1–1 µs, so it's **100–1000× cheaper than the cheapest network hop available.** That's the whole justification.

Two clarifications, because "it's a monolith" is the easy half:

**The components are still cleanly separated — just not by a network.** Gateway, order manager, sequencer and matching engine are distinct modules over a shared-memory event bus. You keep modularity and testability; you give up independent *deployment* and independent *failure*. Recognising that "microservices" bundles four properties and only the last two cost latency is what makes this considered rather than reactionary.

**Everything off the critical path is a normal distributed system** — market data publishing, reporting, settlement, surveillance, archival, all separate services over the network. The boundary is drawn at the latency budget, not at ideology.

The two surprising members of the on-box list are **risk checks and wallet reservation.** Both look like obvious services, and both must happen *before* an order is accepted, so an RPC would be the network hop the design exists to avoid. The resolution: their rules and balances are pushed into the trading process's memory asynchronously, so the *check* is a memory read while the *authority* stays remote. That split — synchronous check against a local replica, asynchronous truth — is the general technique for getting a dependency off a latency-critical path.

---

### 5. What happens if the matching engine crashes mid-session?

A **hot standby** takes over, and it works only because of determinism.

```
Primary  ─┐
          ├── both consume the SAME sequenced event stream
Standby  ─┘
Primary: processes AND publishes.   Standby: processes, publishes NOTHING.
```

The standby is fully caught up at all times, maintaining an identical book. Matching is a **pure function of (book, next sequenced order)** — no clock reads, no randomness, one thread, no unordered iteration — so identical inputs give byte-identical state. Failover isn't a state transfer; it's a **permission change.**

The failure that actually matters isn't a crash, it's a primary that *appears* dead and isn't — a GC pause or a network partition making it miss heartbeats. Promote the standby while the old primary resumes publishing and you get **two engines publishing conflicting fills for the same orders**, both internally consistent and impossible to reconcile.

So leadership is a **lease with an epoch**: every published event carries `(epoch, sequence)`, consumers reject anything from a lower epoch, and promotion increments it. A deposed primary can keep computing into its own memory harmlessly — it just can't be heard. Same fencing pattern as the [message queue's `leader_epoch`](../distributed-message-queue/05-state-metadata-storage.md#why-the-controller-is-a-single-broker), for the same reason: **"I am the leader" is a claim that expires, and a monotonic epoch makes expiry checkable by the recipient.**

And when leadership is genuinely uncertain, the exchange **halts the affected symbols.** Halting is embarrassing and costs money; publishing two divergent books produces fills that don't reconcile and positions that don't exist, and it's unfixable — you cannot un-tell a broker their order filled. **When you can't be sure who's authoritative, unavailable beats wrong.**

---

### 6. You said no database on the critical path. Then how is anything durable?

Persistence is replaced by **replayability**, and it's a deliberate trade rather than an oversight.

The engine holds no durable state. It recovers by loading a snapshot and replaying the sequenced event stream from there. That's the *only* reason it's acceptable for the hot path to touch no disk — and note the causality: **determinism is what buys the latency.** A non-deterministic engine would have to persist its state to recover, putting a write on the hot path, blowing the budget. Determinism isn't a nice property alongside the performance work; it's what makes the performance work possible.

Durability then comes from three places, none on the path:
- **The hot standby**, which holds the same state in a different process/box.
- **An off-path consumer** writing the event stream to the reporting store.
- **Reliable-UDP replication** of the stream to a warm standby box.

The event stream itself lives in `/dev/shm` — a `tmpfs`, so it's pure RAM and never touches disk. Deliberate: mapping a real file would work identically until the kernel wrote back dirty pages, and that would be an unpredictable multi-millisecond stall — the exact tail event the design can't tolerate.

The honest exposure: **a total power loss with a simultaneously-dead standby loses in-flight events.** That's accepted, and it's why the standby is hot rather than warm. It's the same substitution of replication for `fsync` that the [message queue](../distributed-message-queue/02-storage-engine.md#durability-without-fsync-per-message) and [digital wallet](../digital-wallet/04-lld.md#making-one-node-fast) make.

---

### 7. Why not use Kafka as the sequencer? That's exactly what Kafka does.

Conceptually you're right, and that's the useful thing to concede — Kafka is an ordered, replayable, single-writer-per-partition log, which is precisely the abstraction needed.

It's the **wrong latency class by three orders of magnitude.** A Kafka produce-and-acknowledge is 1–10 ms against a total budget of tens of microseconds. Even with `acks=0` you're paying a network hop and a broker's request handling.

So the design keeps Kafka's semantics and reimplements them at the right speed: a **single writer appending fixed-size records into `mmap`'d shared memory.** Records are 64 bytes — one cache line — so the record at sequence N is at byte offset `N × 64`, and random access by sequence is pointer arithmetic with no index at all.

Single-writer is what makes it both fast and correct: no lock, no CAS contention, no ordering ambiguity — the sequence is an integer incremented by one thread. A multi-writer sequencer would need atomic coordination *and* would still have to decide an order, so it'd be slower and no more meaningful.

Kafka absolutely belongs in this system — just downstream, feeding the reporter, surveillance and settlement, where milliseconds are irrelevant.

---

### 8. How do you make sure all market data subscribers get the price at the same time?

**Multicast**, and the reason it's a technical requirement rather than a courtesy is worth spelling out.

With unicast TCP to 300 subscribers you write to each in turn. Subscriber 1 receives the update hundreds of microseconds to a millisecond before subscriber 300. In that window subscriber 1 knows the new price and subscriber 300 is acting on a stale view — so subscriber 1 can trade against subscriber 300's resting orders at prices that no longer hold, and profit systematically. **You've built a machine that lets whoever is early in your iteration order extract money from whoever is late**, based on an implementation detail nobody agreed to. In most jurisdictions that's the venue's problem, not the participant's.

Multicast sends **one** datagram; the switch fabric replicates it in hardware at each branch point. Remaining differences are cable length and NIC processing — tens of nanoseconds, physics rather than software.

The complication is that multicast means UDP, which drops packets silently. A subscriber who misses an update has a **wrong book**, not a slow one, and will keep trading on it. So: **reliable multicast** — sequence numbers on every message so gaps are detected immediately, **NAK-based** recovery (subscribers report only what's missing; ACKing from 300 subscribers would recreate the per-subscriber loop in reverse), retransmission over a **separate unicast channel** so one subscriber's loss can't slow anyone else, and **periodic full snapshots** interleaved with deltas so a badly-behind subscriber resynchronizes instead of requesting thousands of retransmissions.

The subtle point: **retransmission is inherently unfair** — the subscriber who dropped a packet gets it later. That's accepted, because delaying everyone until the slowest acknowledges would make the feed's latency equal the worst subscriber's. The principle is that the *primary* path is simultaneous; recovery is best-effort.

---

### 9. Doesn't colocation destroy the fairness you just designed for?

Partly, yes — and I'd rather say so than defend it as fair.

Multicast goes to real lengths to make delivery simultaneous, and then the exchange sells brokers rack space inside its own datacenter to shave microseconds off their round trip. That reintroduces a latency advantage as a product line.

The defence, stated as a defence: the advantage is **bounded** (speed of light over tens of metres, not an unbounded software edge), **openly priced** (anyone can buy it), and **disclosed**. Compare the unicast-loop problem, where the advantage was invisible, unpriced, and set by an arbitrary iteration order.

So colocation turns latency into an explicit purchasable input rather than an accident of implementation. That's genuinely better than the alternative, and it is **not** the same thing as everyone receiving data simultaneously.

The deeper observation: this whole latency arms race is downstream of a **market-design** decision. Price-time priority rewards speed, so participants invest unboundedly in speed, so the venue must be fast to remain competitive. Change the matching algorithm to a **periodic batch auction** — collect orders over 100 ms and cross them all at one price — and the speed advantage evaporates, along with the justification for this entire one-box architecture. Some venues do exactly that. The design's most extreme constraint traces back to a choice about market microstructure, not a technical necessity.

---

### 10. What's the biggest weakness in this design?

Three, and I'd lead with the one that isn't a feature gap.

**Garbage collection is asserted away rather than engineered away.** The load test demands zero GC pauses, which sounds like a criterion and is actually a **language-selection constraint** masquerading as one. A 1 ms stop-the-world pause is 33× the entire budget and arrives unpredictably, landing on whichever order is unlucky — a p99.99 catastrophe by construction. The real implication is that the critical path must be C++ or Rust, or be written in a managed language in a strictly allocation-free style (pre-allocated pools, primitive arrays, no boxing) which is essentially writing C in that language's syntax. That should be stated as an architectural decision up front, not discovered as a test failure.

**A single symbol cannot scale.** Scaling works across symbols because books are independent — no cross-symbol ordering to preserve. Within a symbol there is **no answer**: you cannot split a book without forking the authoritative order of events, which is the one thing an exchange is defined by. If one symbol's volume exceeded a core's capacity, the only options are a faster CPU or fewer instructions per order, and eventually you run out of both. Every other system in this guide can shard its way out of a hot partition; this one can't, and that's structural.

**Self-trade prevention is missing, and it's a genuine conflict rather than an oversight.** A client whose own buy and sell match creates a wash trade — a regulatory violation. Detecting it means a client-id comparison inside the hottest loop in the system, which adds a branch to code where every instruction is budgeted. So a compliance requirement and the core latency constraint are in direct tension, and this design hasn't priced it. That's the kind of trade-off I'd want to surface to the business rather than resolve unilaterally, because the answer depends on the regulatory exposure, not on the engineering.

Also worth naming briefly: only limit orders are supported (market, stop and iceberg orders each change the matching logic, and icebergs interact badly with the L3 feed that's meant to show queued orders), and multi-region is impossible by design rather than merely hard — an exchange *is* a single authoritative ordering of events, so a regional disaster means a trading halt, which real exchanges accept.
