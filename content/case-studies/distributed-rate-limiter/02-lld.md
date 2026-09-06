# Module 02 — Low-Level Design

![Check-and-consume: the atomic Lua script, and the local-cache-plus-sync optimization on the hot path](diagrams/lld.svg)

The service wraps the `RateLimiter` / `CounterStore` interfaces [the LLD page](../../low-level-design/lld-rate-limiter.md) already defines — this section is about how the *service* uses them, not a new interface.

## Interfaces vs. implementations

- **`RateLimiter`** *(interface)* → **`TokenBucketRateLimiter`** — `allow(key) -> boolean`, per [the LLD page](../../low-level-design/lld-rate-limiter.md)'s own definition. This case study's service is a caller of this interface, not a reimplementation of it.
- **`CounterStore`** *(interface)* → **`RedisCounterStore`** — `checkAndConsume(client_id, cost) -> {allowed, remaining}`, executed as a single atomic Lua script server-side.
- **`LocalCache`** *(interface)* → **`InProcessTTLCache`** — `get(client_id) -> cachedDecision | null`, `set(client_id, decision, ttl)`. This is the one interface this case study adds beyond the LLD page's pair, and it exists purely as a latency optimization in front of `CounterStore` — it holds no authoritative state of its own.
- **`RateLimiterService`** — the orchestrator. Depends on all three interfaces, implements none of the storage or algorithm logic itself.

## Pseudocode for the service's check path

```
RateLimiterService.check(client_id, cost):
    localState = localCache.get(client_id)
    if localState is not None and localState.freshEnough():
        return localState.decide(cost)          # hot path: no network round trip

    result = counterStore.checkAndConsume(client_id, cost)   # atomic Lua script in Redis
    localCache.set(client_id, result, ttl=100ms)
    return result
```

## Error cases worth designing for deliberately

- **`CounterStore` unreachable or times out:** this is not treated as an error the caller has to handle — it's a designed branch (Module 01's fail-open path). The service catches the timeout and returns `{allowed: true, remaining: -1}` rather than propagating a network exception up to the protected service.
- **A client's first-ever check (no existing state):** `checkAndConsume` initializes a fresh bucket at full `bucket_size` rather than treating a missing key as an error — a brand-new client should be allowed immediately, not rejected because its state doesn't exist yet.

**The local-cache-plus-sync optimization**, named explicitly as a trade-off: most of this guide's other pages push for correctness over speed by default, but a rate limiter is one of the few components where being *slightly* wrong in the client's favor is genuinely cheap — a client that occasionally gets 101 requests through on a 100-request budget hasn't broken anything downstream can't absorb. Caching the decision locally for ~100ms and only re-checking Redis periodically shaves the network round trip off most requests, at the cost of the limit being enforced against a slightly stale count during that window. This is a deliberate precision-for-speed trade, not an accident — and it's why a rate limiter can hit a sub-5ms p99 even with a shared store in the loop.

## Concurrency at the code level

`counterStore.checkAndConsume` needs no in-process lock, and this is worth stating explicitly: the limiter-service tier runs on many horizontally-scaled instances, so a language-level mutex around the check would only protect against other threads *on the same instance* — it would do nothing about a different instance checking the same `client_id` a moment later. Correctness comes entirely from Redis executing the Lua script as one uninterruptible unit server-side; the read (current tokens), the compute (refill math), and the write (new token count) never interleave with another script invocation for the same key, regardless of how many limiter-service instances are calling in concurrently.

The one place a local decision *is* made without consulting the store: `localState.decide(cost)` on a cache hit. This is intentionally not "safe" in the strict sense — it's a deliberate, bounded approximation (above), not a correctness mechanism. The actual atomicity guarantee lives entirely in `RedisCounterStore`, and the local cache is never the source of truth for whether a request is actually allowed against the client's real, current budget.

## Design patterns you just used, named

- **Strategy pattern** — `RateLimiter`'s `TokenBucketRateLimiter` and `SlidingWindowRateLimiter` implementations are interchangeable behind one interface, exactly as [the LLD page](../../low-level-design/lld-rate-limiter.md) and [Design Patterns in System Design](../../low-level-design/design-patterns-in-system-design/10-strategy.md) name it — swapping the algorithm never touches `RateLimiterService`.
- **Repository pattern** — `CounterStore` hides where and how counter state is actually persisted; `RateLimiterService` never issues a Redis command directly.
- **Decorator (via the cache layer)** — `LocalCache` wraps access to `CounterStore` without changing its contract: a caller of `RateLimiterService.check()` can't tell, from the interface alone, whether a given call hit the cache or the store — only the latency differs.

## Practice: extend it yourself

Before moving to Database Design, sketch (pseudocode is fine) how you'd add:

1. **Per-user AND per-IP limits, enforced simultaneously** — a single request has to pass both checks to be allowed. Does `RateLimiterService.check()` change shape, or does the caller just invoke it twice with two different keys? What should happen if the per-user check passes but the per-IP check doesn't — is that a different rejection reason the caller needs to see?
2. **A burst allowance on top of a steady rate** — a client's normal rate is 100/sec, but it's allowed to burst to 500 for the first few seconds after being idle. Does this fit inside `TokenBucketRateLimiter` as already designed, or does it need a second bucket? (Hint: look again at what `bucket_size` versus `refill_rate` each actually control.)

Neither has one clean answer — the point is noticing whether the existing interfaces already accommodate the new requirement, or whether a genuinely new concept (a second bucket, a composite decision) has to be introduced.
