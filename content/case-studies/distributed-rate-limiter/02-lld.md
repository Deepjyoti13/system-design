# Module 02 — Low-Level Design

![Check-and-consume: the atomic Lua script, and the local-cache-plus-sync optimization on the hot path](diagrams/lld.svg)

The service wraps the `RateLimiter` / `CounterStore` interfaces [the LLD page](../../low-level-design/lld-rate-limiter.md) already defines — this section is about how the *service* uses them, not a new interface.

```
RateLimiterService.check(client_id, cost):
    localState = LocalCache.get(client_id)
    if localState is not None and localState.freshEnough():
        return localState.decide(cost)          # hot path: no network round trip

    result = CounterStore.checkAndConsume(client_id, cost)   # atomic Lua script in Redis
    LocalCache.set(client_id, result, ttl=100ms)
    return result
```

**The local-cache-plus-sync optimization**, named explicitly as a trade-off: most of this guide's other pages push for correctness over speed by default, but a rate limiter is one of the few components where being *slightly* wrong in the client's favor is genuinely cheap — a client that occasionally gets 101 requests through on a 100-request budget hasn't broken anything downstream can't absorb. Caching the decision locally for ~100ms and only re-checking Redis periodically shaves the network round trip off most requests, at the cost of the limit being enforced against a slightly stale count during that window. This is a deliberate precision-for-speed trade, not an accident — and it's why a rate limiter can hit a sub-5ms p99 even with a shared store in the loop.
