# Module 06 — Interviewer Q&A

---

### 1. Why an append-only log instead of a queue with a `consumed` flag?

Because of one measurement. On the same commodity drive: random I/O gives **0.61 MB/sec** (150 IOPS × 4 KB), sequential gives **~150 MB/sec**. That's **244×**, and the requirement is 500 MB/sec — impossible with random access (you'd need ~800 drives for the write path), comfortable with sequential (about four).

A `consumed` flag is random-access mutable state: every acknowledgement is a random write to a random row. That single design choice locks you into the 0.61 MB/sec regime.

The structural insight is that **per-message delivery state is what forces random I/O**, so you remove it. Replace "which messages have been consumed" with **one integer per partition per consumer group** — the offset. Now:

- Consuming a message mutates nothing; it moves a cursor.
- Retention and replay come free, because reading doesn't destroy.
- Multiple independent consumer groups cost one integer each, not a copy of the data.
- Every write is an append and every read is a forward scan.

The queue-with-a-flag design also can't satisfy the stated retention requirement at all — deleting on acknowledgement means a second consumer can never read the same message, and a broken consumer can't be fixed and replayed.

---

### 2. How do you guarantee ordering?

**Within a partition, and only within a partition.** Order is the byte order of the file, so there is literally nothing to maintain — the leader is the single writer, it assigns offsets sequentially, and readers scan forward.

Across partitions there is **no** ordering, deliberately. That's the price of parallelism, and you buy ordering back selectively with a **partition key**: `partition = hash(key) % n` means all messages for `user-42` land in one partition and are therefore totally ordered relative to each other, while messages for different users process in parallel.

So the real answer is that partitioning lets you **choose the granularity at which you trade ordering for parallelism.** Order everything (one partition, no parallelism), order nothing (random partitioning, maximum parallelism), or order per entity (key-based, which is almost always what you want).

Two consequences I'd flag:

- **One consumer per partition per group is the same constraint viewed from the other side.** Two consumers on one partition would process nondeterministically, and you couldn't track a single offset for two readers. So ordering is exactly what caps consumer parallelism at the partition count.
- **Changing partition count breaks per-key ordering permanently**, because `hash(key) % n` changes for every key. See answer 9.

---

### 3. A producer gets an ack. Guarantee it's never lost.

It depends entirely on `acks`, and that's deliberately the producer's choice:

| `acks` | Waits for | Loss window |
|---|---|---|
| `0` | Nothing | Any failure. No error is even reported. |
| `1` | Leader's local write | Leader dies before a follower fetches → **acknowledged message lost** |
| `all` | Every member of the ISR | Only if all ISR members lose data at once |

`acks=1` is the dangerous middle because it *looks* durable — the producer got a success — and the loss is silent. The window is one fetch interval, ~500ms, but real.

The subtlety worth raising unprompted: **`acks=all` alone is not enough.** The ISR shrinks when followers fall behind, and an ISR of 1 means "acknowledged by the leader alone" — `acks=1` wearing a misleading name, and it degrades exactly when the cluster is unhealthy. So you need:

```
replication.factor  = 3
min.insync.replicas = 2       ← writes FAIL if the ISR drops below this
acks                = all
```

That last setting is the design **choosing unavailability over silent durability loss**, and it's the most commonly mis-set option in real deployments.

Also worth noting: durability here comes from **replication, not `fsync`.** An `fsync` costs 1–10ms, so at 488k messages/sec you can't do one per message. Messages sit in page cache on three machines in three racks; that's the guarantee. Simultaneous power loss across all three racks is an explicit, stated exposure.

---

### 4. Why can't consumers read the newest message on the leader?

Because it might not survive, and this is what the **high watermark** is for. Two offsets exist per partition:

- **Log End Offset** — the last offset the leader has written.
- **High Watermark** — the last offset replicated to *every* ISR member. Everything ≤ HW is committed.

Consumers read only up to the HW. Here's the failure it prevents:

> A consumer reads message 105 (past the HW), processes it, commits. The leader dies before 104–105 reach any follower. A follower at 103 becomes leader. **Messages 104 and 105 now exist nowhere.** The consumer has processed and committed a message the system says was never written — and if it was "charge this card," that's already happened and is unrecoverable from the log.

Worse, offsets 104 and 105 will be *reassigned* to different future messages, so the log's history has changed underneath every consumer tracking positions.

Restricting reads to the HW means **a consumer can never observe a message that might later vanish** — the log's visible history is immutable.

This is also why consumers read from the leader and not from followers: a follower's view of the HW lags by a fetch round trip, so a follower-served read could expose an offset committed on stale information. The cost is that followers are idle insurance, mitigated by spreading *leadership* evenly across brokers.

---

### 5. Push or pull to consumers?

**Pull**, with long polling.

The decisive reason: **the broker cannot know a consumer's processing capacity, and it changes constantly.** A consumer doing a DB write per message handles 1,000/sec; one running ML inference handles 10/sec; both may subscribe to the same topic. Under push the broker either overwhelms the slow one or throttles to the slowest, penalising everyone. Under pull each consumer sets its own rate, so **backpressure is automatic** — a slow consumer just has growing offset lag, which is a metric rather than an incident.

Pull also makes several things fall out for free:

- **Replay is the same call** with a different offset. Under push you'd need a separate mechanism, because "what to send next" would be server-side state.
- **Batch sizing is the consumer's choice** (`max_bytes`), so it can adapt.
- **Replication reuses the consumer path.** Followers are just privileged consumers that pull from the leader, so one code path serves both — including `sendfile()`. That's why the pull model is cheaper to build, not just to run.

The two costs are both handled: empty fetches would mean busy-polling, fixed by `max_wait_ms` holding the request open (long polling); and the residual idle-latency cost is bounded by that timeout.

---

### 6. Walk me through what happens when a consumer crashes.

A rebalance, and the important part is the fencing.

```
1. The consumer stops heartbeating. After session.timeout.ms the coordinator
   (the broker at hash(group_id)) declares it dead.
2. The coordinator marks the group rebalancing and BUMPS THE GENERATION ID.
   Existing members learn this from the error code on their next heartbeat.
3. All members re-send JoinGroup.
4. The coordinator picks one member as group leader.
5. That member — a CLIENT — computes the assignment plan and returns it via SyncGroup.
6. The coordinator distributes each member's slice.
7. Members resume from the committed offsets.
```

**Why a client computes the assignment:** assignment strategy is application policy (range, round-robin, sticky, rack-aware, or something bespoke that co-locates related partitions). Putting the planner in the client lets a team ship a custom assignor without upgrading the cluster. The broker only handles membership and distribution — the part that must be consistent.

**Why the generation ID is essential.** Consider a consumer that had a long GC pause, was declared dead, had its partitions reassigned, then woke up and committed:

> Without fencing, that commit overwrites the *new* owner's progress — rewinding it (reprocessing) or fast-forwarding it (message loss). The zombie would also keep processing messages it no longer owns.

Commits and heartbeats carry the generation ID; stale ones are rejected with `ILLEGAL_GENERATION`. **A rebalance protocol without a fencing token is broken**, and it's the detail most often missing from whiteboard designs.

The flaw I'd volunteer: this is **stop-the-world**. All members must rejoin before the plan is computed, so a 100-consumer group stalls entirely because one consumer restarted — 98 of them then get the identical assignment back. During a rolling deploy you pay it 100 times. Fixes: **sticky assignment** (removes the wasted work), **incremental cooperative rebalancing** (removes the stall — members revoke only what they lose), and **static group membership** (a consumer returning with the same member ID inside the session timeout triggers no rebalance at all, turning a 100-rebalance deploy into zero).

---

### 7. Can you do exactly-once?

**Exactly-once delivery is impossible**, and I'd start there. Any network can lose a delivery acknowledgement, so the sender can't distinguish "message lost" from "ack lost" and must choose between retrying (duplication) and not (loss). No protocol escapes it.

**Exactly-once *processing* is achievable** — at-least-once delivery plus deduplication, so the observable effect happens once. Three mechanisms for three different duplication sources:

**1. Idempotent producer** (fixes duplication on write). Each producer gets an ID; each message carries a per-partition sequence number; the broker discards already-seen sequences. Without it, a producer whose ack was lost in flight retries and writes the batch **twice**. Nearly free — always turn it on.

**2. Transactions** (fixes atomicity across partitions). Messages are written but marked uncommitted; a transaction marker commits or aborts them; consumers set `isolation.level=read_committed`. The critical call is `sendOffsetsToTransaction` — **the offset commit joins the transaction**, which is what closes at-least-once's window where output was written but the input offset wasn't committed.

**3. Idempotent consumers** (fixes duplication of *effects*) — and **this is where exactly-once actually comes from in practice.** Broker transactions only cover effects inside the log. The moment a consumer writes to a database, a payment API, or an email provider, the log's transaction can't include it. So dedupe on a business key, or lean on a `UNIQUE` constraint.

My recommendation: idempotent producer always (free, removes a real source), transactions for Kafka-to-Kafka stream processing (real cost: markers, `read_committed` buffering, coordinator overhead), and **idempotent consumers regardless** — because that's the only one covering the external effects that matter. Once consumers are idempotent, at-least-once gets you the same guarantee for far less machinery.

---

### 8. One consumer group replays two weeks of data and the whole cluster slows down. Why?

**Page-cache eviction**, and it's this design's most interesting failure mode.

There's no application-level message cache — brokers run a small heap and let the OS page cache do the work. That's deliberate: normal consumers read the **tail**, data written seconds ago that's still in page cache from having just been written, so a tail fetch does **zero disk reads**. An application cache would duplicate what the kernel already holds and add GC pauses to p99.

But the cache is **shared and kernel-managed**. A consumer replaying from offset 0 streams 605 TB of cold segments through it, evicting the hot tail. Suddenly every well-behaved consumer's reads go to disk — 0.61 MB/sec territory — and cluster throughput collapses. One badly-behaved consumer degrades everyone.

The strategy's strength and weakness are the same property, which is worth saying plainly rather than presenting the page cache as a pure win.

Mitigations, in increasing order of effectiveness:
- **Rate-limit historical reads** (byte-rate quotas per client). Helps, doesn't solve.
- **Dedicated replay brokers** — extra followers promoted to serve replay traffic, so cold reads never share a page cache with tail traffic.
- **Tiered storage** — offload sealed segments older than ~24h to object storage. Replay reads then come from S3 and never touch broker page cache at all. This fixes the problem structurally *and* cuts the cost of 605 TB × 3 on broker-local disk, which is why it's the single highest-value extension to this design.

A related inversion worth knowing: **consumer group count, not producer rate, often saturates a cluster first.** Ten groups on one topic means 10× the read bandwidth of the write bandwidth.

---

### 9. How do you add partitions to a running topic?

Mechanically easy, semantically disruptive — and the disruption is the answer.

```
Before: 4 partitions.  hash("user-42") % 4 = 2
After:  8 partitions.  hash("user-42") % 8 = 6

user-42's history is now split across partitions 2 and 6, consumed by different
consumers, in NO GUARANTEED RELATIVE ORDER.
```

**Per-key ordering breaks for every key, permanently, across the boundary.** For a topic where ordering doesn't matter, fine. For a CDC stream or an event-sourced aggregate it's a correctness break, and the only safe migration is a new topic at the target partition count plus a consumer migration.

**Decreasing is worse.** A partition's data can't be deleted (still in retention, consumers may need it) and can't be merged (merging two ordered logs yields an arbitrary interleaving). The best available approach is a graceful decommission: producers stop writing to it, consumers keep reading it, and it's removed only after the retention period expires naturally — a **two-week wind-down** at this design's settings.

So partition count is effectively a **one-way door**, which is why I'd over-provision to ~100 up front. And the number is driven by the larger of two things: throughput need (500 MB/sec ÷ ~50 MB/sec per partition = 10) and desired consumer parallelism (a group can never usefully exceed the partition count). The second usually dominates.

---

### 10. Why do you need ZooKeeper/etcd? Can't the brokers agree among themselves?

They could in principle, and modern designs do — but the requirement is specific, so let me state what consensus is actually buying:

> If two brokers simultaneously believed they led partition 3, both would accept appends and assign overlapping offsets. The log would **fork**. Since an offset is a message's only identity, there'd be two different messages at offset 1,500, both acknowledged, with no way to reconcile. Unlike lost data, this isn't even detectable after the fact.

So the cluster map needs **linearizable** consensus, plus leader election and ephemeral sessions for failure detection. That's a different engine from the log: consensus costs a quorum round trip per write, which is fine at one write per leadership change and fatal at 488k messages/sec. Merging them makes either the log slow or the map unsafe.

**Why the map can't just be a compacted topic**, given that's exactly how [offsets are stored](./05-state-metadata-storage.md#consumer-offsets-the-compacted-log): bootstrapping. The map tells you which broker leads which partition — storing it in a partition means needing the map to find the map.

The mechanism that makes it work in practice is **fencing via `leader_epoch`**, incremented on every leadership change. A deposed leader that hasn't yet learned it was deposed (long GC pause, healed partition) gets `STALE_EPOCH` and steps down. Same pattern as the consumer generation ID, for the same reason: **"I am the leader" is a claim that expires, and a monotonic epoch makes expiry checkable by the recipient.**

The honest limitation: **controller failover scales badly.** A new controller must load full metadata from the external store before acting, so failover time grows with partition count — minutes at tens of thousands of partitions, during which no failover or rebalance can happen. That's the known wall of this architecture, and it's why newer designs move metadata into a self-managed internal Raft log, removing the external dependency and making failover incremental. Notably, that direction doesn't abandon consensus — it just stops outsourcing it.
