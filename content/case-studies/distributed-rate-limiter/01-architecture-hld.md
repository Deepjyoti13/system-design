# Module 01 — Architecture & High-Level Design

![Distributed rate limiter architecture: edge and per-service placement, sharded counter store, fail-open path](diagrams/hld.svg)

## Monolith vs. microservices

The limiter is pulled out as its own standalone service, never re-implemented as a library embedded inside each protected service, for a reason that's about the *counter*, not the code: every caller has to agree on the same count for the same client, at the same instant, or the limit isn't actually a limit — it's N independent guesses. Embedding rate-limiting logic in each service still leaves them all needing to hit the same shared counter store, so the only thing embedding would save is the network hop to a shared decision point, while it would cost every service team its own copy of the algorithm, the fail-open policy, and the tier-config lookup to keep in sync. Centralizing it means the algorithm, the fail-open/fail-closed policy, and the tier configuration are each defined exactly once, and every protected service gets identical behavior for free.

There's a second reason the seam holds even for a small platform: a rate limiter's failure mode is platform-wide by construction — see Load Handling below — so it earns the operational overhead of being its own service (its own on-call story, its own scaling story) in a way a narrower, single-team feature usually wouldn't. If you're protecting one service with one obvious traffic pattern, an in-process limiter is a legitimate, simpler starting point; the moment a second service needs the same protection against the same clients, the shared-store requirement above makes a standalone service the lower-total-cost option, not a premature abstraction.

**Placement — both, not either:** at the [API gateway](../../hld-building-blocks/api-gateway.md) for external-client abuse protection, and per-service internally for one internal caller overwhelming another — exactly the "real systems commonly need both" point [Rate Limiting](../../hld-building-blocks/rate-limiting.md) already makes. This case study is that same limiter, deployed as its own scaled-out service both call into, rather than logic duplicated in each place.

**The shared counter store:** Redis, sharded across nodes by `client_id` via [consistent hashing](../../hld-building-blocks/consistent-hashing.md) — so adding a shard as the client count grows only reshuffles one slice of clients, not all of them. Each shard is itself replicated (cross-ref [Replication & Consensus](../../hld-building-blocks/replication-consensus.md)) so one node dying doesn't erase that shard's counters.

**Avoiding being the bigger SPOF:** what happens if the counter store is unreachable is a deliberate choice, not a default. Fail-open (allow the request through unchecked) protects the availability of everything downstream at the cost of temporarily losing the limit — an AP-leaning choice in [this guide's CAP framing](../../foundations/latency-throughput-cap.md). Fail-closed (reject everything) protects against a flood at the cost of a full outage for every legitimate client too. **Default to fail-open** for most traffic, since an unthrottled few minutes is almost always cheaper than a total outage of the thing being protected — and fail-closed only for the specific limits that exist purely for cost/abuse protection (e.g. a paid third-party API you're billed per-call for), where an unbounded burst has a real dollar cost attached.

## Per-path walkthrough

**Check-and-consume path (the only path)** — `Caller service → LB → Limiter Service (LocalCache lookup, ~100ms freshness) → [on miss] CounterStore (atomic Lua script: read, refill, decide, write) → LocalCache populated → {allowed, remaining, retry_after_ms} → Caller service`. There is no separate "write path" the way a CRUD system has one — every check is simultaneously the read of the current state and the write of the new one, which is exactly why the atomicity has to live at the store (Module 02 covers this at the code level).

**Fail-open path (the store is unreachable)** — `Caller service → LB → Limiter Service (CounterStore call times out) → {allowed: true, remaining: -1}`, bypassing the decision entirely rather than blocking on a store that isn't answering. This path is a first-class part of the design, not an exception handler bolted on afterward — it's what keeps a Redis outage from becoming a platform-wide outage.

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Rate-limiting algorithm | Token bucket | Fixed window counter | Fixed window allows a burst of up to 2x the limit right at a window boundary (a client sends its full quota at 11:59:59 and another full quota at 12:00:00); token bucket has no window edges to burst across |
| Rate-limiting algorithm | Token bucket | Sliding window log | A sliding log needs a timestamp per request to stay exact, which is far more storage per client at this guide's scale (10M tracked clients); token bucket needs a fixed ~16 bytes of state regardless of request volume |
| Store outage policy | Fail-open by default | Fail-closed by default | A rate limiter sits in the critical path of every protected service; fail-closed turns a Redis blip into a full-platform outage, which is a worse failure than temporarily under-enforcing a limit |
| Placement | Both edge (gateway) and per-service | Edge-only | Edge-only protects against external abuse but does nothing about one internal service overwhelming another — a real failure mode this guide's [Rate Limiting](../../hld-building-blocks/rate-limiting.md) page names explicitly |
| Read freshness | Local-cache-plus-sync (~100ms staleness) | Always read Redis directly | A rate limiter is one of the few components in this guide where being slightly wrong in the client's favor is cheap; caching shaves the network round trip off most requests at an acceptable, bounded cost (Module 02) |

## Load Handling

- **Peak-vs-average tolerance:** this service's load isn't its own — it's the *aggregate* of every protected service's combined traffic, which means it has to scale ahead of any single service's growth, not alongside it. The 1.5x peak factor from Capacity Estimation (~750K/sec) is absorbed by horizontally scaling the stateless limiter-service tier; the harder question is always the Redis shard count, not the app tier.
- **Where backpressure kicks in first:** at the `CounterStore` call itself — if a specific shard is saturated or slow, the limiter service applies a bounded timeout (a few milliseconds, well inside the 5ms decision budget) rather than letting one slow shard stall every caller waiting on a decision.
- **What gets shed under overload:** never the decision itself — a timed-out `CounterStore` call falls through to the fail-open path (above) rather than blocking the caller or returning an error. The thing that "sheds" is precision, not availability: a request is allowed through unchecked rather than the whole check being refused.
- **Autoscaling lag:** the limiter-service tier reacts to load within its normal autoscaling horizon (1-3 minutes); the seconds-scale gap before that kicks in is absorbed by existing headroom on already-provisioned Redis shards, not by scaling reflexes — the load-test target below is calibrated to that same headroom.
- **Load-test target:** sustain 1M decisions/sec for 10 minutes with p99 latency still under 5ms and zero shard exceeding 70% of its provisioned ops/sec.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| Two requests for the same client arrive at two different limiter-service instances at the same instant | Both instances call the same Redis shard's atomic Lua script (read, refill, decide, write as one uninterruptible unit); Redis's single-threaded script execution serializes the two calls *at the store*, not at either instance | The instance whose script ran second sees the post-decrement token count from the first — a correct rejection, never a double-allow |
| A request arrives exactly at what would be a fixed-window boundary | Token bucket has no window edges — refill is computed continuously from elapsed time since `last_refill_ms`, not reset at a clock boundary | Nothing special; there is no boundary to race against, which is the whole reason this design chose token bucket over fixed window (Trade-offs, above) |
| A client's tier config changes (free → paid) while a check is in flight | `bucket_size`/`refill_rate` are read fresh from the same key on every check (Module 03's schema), not cached separately from the token state | The very next check after the config write sees the new tier — never a stale limit enforced against an already-upgraded client |
| The local cache and Redis briefly disagree (a request lands inside the ~100ms staleness window) | Not resolved as a race at all — it's an accepted, bounded trade named explicitly in Module 02, not a bug requiring a fix | A client gets a few extra allowed requests inside the staleness window, bounded by the cache TTL, never unboundedly |

## Scaling & Reliability

- **Horizontal scaling:** the limiter-service tier is stateless and scales behind its own load balancer by decision volume; Redis shard count scales independently, driven by tracked-client count and ops/sec rather than by request volume alone.
- **Circuit breaker:** the `CounterStore` call is wrapped in a circuit breaker (cross-ref [Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md)) — if a shard starts timing out systematically, the breaker trips and every subsequent check for that shard's clients takes the fail-open path immediately, rather than each request separately waiting out its own timeout.
- **Retries:** deliberately minimal on the check path — a retried check that lands after the original's timeout risks doing redundant work for a decision that's already time-sensitive (a 5ms budget), so a timeout goes straight to fail-open rather than a retry-then-timeout sequence that would blow the latency budget.
- **Graceful degradation:** this *is* the fail-open path from Module 01's opening discussion, worth naming again here explicitly as the system's reliability strategy, not just its store-outage handler: the limiter degrades from "enforced" to "unenforced" rather than from "available" to "unavailable," which is the entire point of choosing fail-open as the default.
- **Multi-region:** not built here, and worth naming as a real gap — see below.

## What you'd revisit as this grows

- **Multi-region counter consistency.** A single-region Redis deployment is a single point of regional failure, and a client hitting endpoints in two regions would need its counter state either replicated cross-region (with the staleness that implies) or partitioned by region (which changes what "the limit" actually means for that client) — a genuinely harder problem this design doesn't take on.
- **Hierarchical limits.** This design checks one limit per call; a mature system often needs per-user *and* per-IP *and* per-API-key limits simultaneously, each with its own bucket, all evaluated for the same request (Module 02's practice exercises pick this up).
- **Dynamic tier changes at scale.** Tier config is read fresh per check (cheap at this guide's scale), but a platform with millions of distinct tier configurations might want a dedicated config-caching layer in front of Redis rather than relying on Redis itself to also serve as the config store.
- **Adaptive limits.** A fixed `bucket_size`/`refill_rate` per tier works until the platform wants to auto-adjust limits based on observed abuse patterns or backend health — a real capability, deliberately out of scope for this module's fixed-configuration design.
