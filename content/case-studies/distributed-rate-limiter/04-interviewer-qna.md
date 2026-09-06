# Module 04 — Interviewer Q&A

**1. What happens when two requests for the same client hit the same resource at the same instant?**
Both calls resolve to the same Redis shard and the same key; Redis's single-threaded script execution makes the Lua check-and-consume atomic, so the two requests are serialized *at the store* regardless of which limiter-service instance received them — one sees the token available, the other sees it already spent. No lock, distributed or otherwise, is needed on the calling side.

**2. What happens when traffic spikes 10x for an hour?**
The limiter-service tier is stateless and scales out immediately behind its load balancer. The real question is whether the Redis shards can absorb 10x the ops/sec — they're provisioned with headroom (the load-test target in Module 01), and if a shard still saturates, the local-cache-plus-sync layer absorbs more of the read traffic by extending its TTL slightly, trading a bit more staleness for taking load off Redis exactly when it's most needed.

**3. Why is the rate limiter's own availability treated as a bigger design concern than a normal service's?**
Because unlike most services, this one sits in the critical path of *every other service*, so its failure mode doesn't just take down one feature — a naive fail-closed default would turn a Redis blip into a full-platform outage. That's why the fail-open default in Module 01 is a deliberate, load-bearing decision, not an afterthought.

**4. Could you avoid the shared store entirely and rate-limit per-instance instead?**
Only by accepting a limit that's actually `N × intended_limit` for N instances, since each instance would see only its own slice of a client's traffic — this is exactly [the distributed rate limiter problem](../../hld-building-blocks/rate-limiting.md#the-distributed-rate-limiter-problem) the concept page names, and it's why a shared store is unavoidable once there's more than one instance.

**5. How would you handle a "noisy neighbor" client whose traffic pattern is so bursty it keeps forcing cache invalidation for everyone else?**
It wouldn't — the local cache and Redis key are both scoped per `client_id`, so one client's bursty pattern only churns *its own* cache entry and Redis key; there's no shared state between different clients' rate limits to invalidate.

**6. Would a client tier upgrade (free → paid) take effect immediately, or only for new buckets?**
Immediately, on the very next check — `bucket_size`/`refill_rate` are stored alongside the token state itself (Module 03's schema) rather than in a separate config table, so a tier change is just an overwrite of those two fields, read fresh on every check without needing the bucket to fully drain first.

**7. What's the actual cost of the local-cache-plus-sync optimization going wrong — could a client abuse the 100ms staleness window?**
A client could in principle time requests to land inside the stale window and get slightly more throughput than its exact budget — but the ceiling on that abuse is bounded by the cache TTL (100ms of extra budget, not unbounded), which is why the TTL is chosen deliberately short: enough to cut most round trips, not enough to meaningfully widen the limit.

**8. Would you ever return `allowed: true` with a warning instead of `false`, rather than a hard reject?**
Only for internal, trusted callers under the fail-open default already described — the response shape (`allowed`, `remaining`, `retry_after_ms`) already gives a caller everything it needs to self-throttle proactively before ever being hard-rejected, which is the softer signal a well-behaved internal client should be watching for.

**9. Why token bucket over a sliding window log, given a sliding log is more precise?**
Precision isn't free here: a sliding window log needs to retain a timestamp per request to stay exact, which scales with request *volume* per client rather than staying fixed — at this guide's scale (10M tracked clients, hundreds of requests/sec each), that's a genuinely larger memory footprint than token bucket's fixed ~16 bytes of state per client regardless of how many requests it's made. Token bucket's imprecision is bounded and acceptable (see Q7); a sliding log's precision isn't worth its cost at this volume.

**10. How would you test the fail-open path without waiting for a real Redis outage?**
Inject a fault at the `CounterStore` interface boundary — a test double that simulates a timeout on `checkAndConsume` — and assert `RateLimiterService.check()` returns `{allowed: true, remaining: -1}` within the decision latency budget. Because the service depends on `CounterStore` as an interface (Module 02), the real Redis client never needs to be involved in this test at all, the same interface-over-implementation benefit this guide gets from testing any of its other pluggable dependencies.
