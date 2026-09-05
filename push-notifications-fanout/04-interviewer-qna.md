# Module 04 — Interviewer Follow-Up Bank

Ten questions an interviewer would actually ask about this specific design, each answered from a decision already made in modules 01–03 — not a generic textbook answer.

**1. What happens when two dispatch workers pick up the same job?**
A Kafka consumer-group rebalance can redeliver a job after it's already been processed but before the offset committed. Both workers attempt `RedisDedupGuard.tryClaim`, which is a `SETNX` with a 24h TTL — atomic across every worker instance. Exactly one wins and sends; the other silently no-ops. This has to be a distributed claim, not an in-process lock, because the dispatch pool runs on many instances by design (module 02).

**2. What happens when traffic spikes 10x for an hour (not the planned 14x burst, an *unplanned* one)?**
Ingest is decoupled from dispatch by Kafka, so ingest throughput never degrades. Queue depth absorbs the spike first; if dispatch lag crosses 2 minutes, autoscaling adds worker instances (2–3 minute warm-up, during which the queue keeps absorbing). Nothing is dropped — bulk notifications get later, not lost — and transactional traffic is unaffected because it has its own topic and worker pool, never shared capacity with bulk (module 01, Load Handling).

**3. APNs goes down for 20 minutes. What happens to Android users?**
Nothing — `ApnsProvider` and `FcmProvider` are independent implementations behind the same `NotificationProvider` interface, each wrapped in its own circuit breaker. The breaker trips for APNs specifically; its jobs requeue with backoff and eventually hit the dead-letter queue if APNs doesn't recover in time. FCM's queue, workers, and circuit breaker are untouched.

**4. A celebrity with 100M followers posts. Why doesn't fan-out become a single bottleneck?**
Two things prevent it: the `FollowerGraphTargetResolver` paginates the follower list rather than loading it in one shot, and every resulting per-device job is partitioned onto the dispatch topic by `hash(device_token)` — not by user or by event — so the 100M jobs spread evenly across every worker in the bulk pool instead of funneling through whichever single process resolved the event.

**5. Is this exactly-once or at-least-once delivery — and why not the other one?**
At-least-once at the infrastructure level (Kafka can redeliver a job) with a bound on user-visible duplicates via the Redis dedup claim — that's a different, weaker guarantee than true exactly-once, and it's chosen deliberately: real exactly-once would need a distributed transaction spanning the queue and the provider call, and providers themselves don't offer transactional sends. A rare duplicate push is an acceptable cost; the infrastructure to eliminate it entirely isn't worth its cost for this use case.

**6. A user requests account deletion (GDPR). What happens to their data in this system?**
`device_tokens` rows for that user are deleted synchronously — it's a small, point-lookup-keyed row set, cheap to remove immediately. `delivery_log` is different: it's an append-only, time-partitioned analytics store with 90-day retention; scrubbing a specific user's rows out of it synchronously would be expensive against its access pattern, so that erasure runs as an async scheduled job instead. That's an explicit, named trade-off (immediate vs. eventual erasure), not an oversight.

**7. Where does the cost actually go at 5B notifications/day, and how would you cut it?**
Provider API calls dominate cost at this volume. Batching bulk sends (FCM multicast, APNs HTTP/2 multiplexed streams) cuts the per-call overhead that per-notification calls would multiply by 5B. The second lever is the worker fleet itself — autoscaling means capacity is paid for at the 58K/sec average most of the time, not provisioned permanently for the 833K/sec peak.

**8. Could you skip the queue for transactional traffic and call providers directly from the Ingest API, to shave latency further?**
It's a legitimate alternative to name, but the trade is losing per-provider retry/backoff and circuit-breaking, which the queue gives essentially for free. The better version of "faster" is what this design already does: keep the queue, but give transactional its own near-empty topic and a hot, reserved worker pool — the 2-second SLA already assumes minimal queueing time, not zero.

**9. What happens if the Redis dedup cache itself goes down?**
The design fails *open*: if `tryClaim` can't reach Redis, treat it as "not claimed" and send anyway, rather than blocking all delivery on a caching dependency. The cost is a bounded burst of duplicate sends during the outage — accepted, because "duplicate push" is a far smaller problem than "no push at all," and the outage is expected to be short relative to the 24h dedup TTL anyway.

**10. A user mutes a topic while a broadcast to that topic is already fanning out. Does the mute apply?**
Only to jobs not yet enqueued. `TargetResolver` checks mute status at resolution time, but once a per-device job is on the dispatch topic, dispatch workers don't recheck it — re-checking would mean a synchronous lookup on every single send at 833K/sec for a case that's rare and low-stakes. The system is "mostly consistent, not real-time" here, and that's an accepted trade, not a gap.
