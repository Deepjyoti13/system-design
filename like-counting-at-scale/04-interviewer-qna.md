# Module 04 — Interviewer Follow-Up Bank

Ten questions a real interviewer would push on after seeing modules 00–03, each answered from a decision already made rather than a generic textbook answer.

**1. What happens when two requests hit the same resource at the same instant?**
Depends which resource. Two different users liking the same post at the same instant isn't a conflict at all — Redis `INCR` is atomic and commutative, so both increments apply independently with nothing to coordinate. The same user double-tapping (or a client retry) on the *same* post is a conflict the ledger resolves via a conditional write keyed by `(post_id, user_id)`: the second write is a no-op that returns the same result, not an error.

**2. What happens when traffic spikes 10x for an hour?**
Platform-wide, 10x average (~1.15M/sec) is an ordinary horizontal-scaling problem: more Like Service instances, more Kafka partitions, more Redis nodes. The design doesn't rely on autoscaling to survive the first minute of a spike, though — the per-user rate limiter and Kafka's own buffering absorb the burst while new capacity provisions over the following 1–3 minutes.

**3. What if a viral post gets 10x *this design's* per-key assumption — 500,000 likes/sec on one post?**
The fixed 16-way counter shard would become the bottleneck at that scale, and this is explicitly named in module 01's "what you'd revisit" — a real system needs to detect a hot key and dynamically increase its shard count, which this design doesn't build. The honest answer here is "the current design has a ceiling, and here's exactly where it is," not a claim of infinite scalability.

**4. What happens if a Redis node holding one of a post's counter shards goes down?**
The read path falls back to `posts.like_count` for that post (module 01's dashed fallback path) — slower and reintroducing some contention, but functioning. The write path can skip the synchronous increment for that shard and rely on the Kafka event alone; the count catches up once the aggregator processes it or the node recovers.

**5. How do you guarantee exactly-once here, given Kafka is at-least-once?**
The design doesn't try to make delivery exactly-once — it makes the *effect* idempotent instead, which is the easier and more robust target. The ledger's conditional write means a redelivered toggle event is a no-op. The aggregator commits Kafka offsets only after a successful durable write and dedupes by message offset, so a redelivered batch after a crash doesn't double-apply.

**6. A user requests account deletion (GDPR). What has to happen?**
Every ledger row keyed by that `user_id` needs deletion — straightforward since `user_id` is the ledger's clustering key, a scan within each partition the user has a row in (or, if this becomes a frequent operation, the inverted user→posts index named as a gap in module 03 earns its keep here). `posts.like_count` values need no correction: they're aggregate counts, not references to the deleted user, so they're unaffected — this is a case where the denormalization actually simplifies the deletion story.

**7. Why not just skip Redis and write through to `posts.like_count` synchronously — isn't that simpler?**
That's arguing module 01's central trade-off the other way, and the answer is the system's whole premise: a synchronous write-through reintroduces the exact row-lock contention on `posts` that made this an interesting problem, capping a single viral post's like throughput at whatever one row's lock can process. It would be simpler, and it would fail at exactly the scale this design targets.

**8. How would you load-test this before launch?**
Against the load-handling target from module 01: sustain 60,000 write ops/sec against one artificially hot `post_id`, p99 toggle latency under 150ms, zero write failures, for ten minutes — then repeat with a Redis node killed mid-test to confirm the fallback path holds under load, not just in isolation.

**9. What's the actual cost driver at this scale, and how would you reduce it?**
The Kafka topic and the Redis Cluster are the two components sized for peak hot-key load rather than average load — most of that capacity sits idle most of the time. Dynamic, hot-key-aware shard counts (module 01's named future work) would let both scale down for the 99.9% of posts that never go viral, instead of provisioning every post for a celebrity-post scenario.

**10. Why is the toggle a separate service from the rest of the post platform, and would you make the same call at 1/100th the scale?**
No — at a smaller scale, folding the toggle into a general Post Service is the right call; the split is justified in module 01 specifically by one post being able to demand 50,000 writes/sec, a profile the rest of the platform doesn't share. Below that scale, the split adds an operational service boundary with nothing to show for it — the same "match complexity to actual scale" principle module 04's own practice problems (in the URL-shortener project) call out for the Parking Lot System.
