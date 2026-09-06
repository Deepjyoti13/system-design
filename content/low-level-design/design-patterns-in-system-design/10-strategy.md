# Strategy

![Strategy: OrchestrationService depending only on the ProcessorClient interface, unaware which concrete processor answers a call](diagrams/strategy.svg)

## The Problem It Solves

Several interchangeable *algorithms* solve the same job — a rate-limiting algorithm, a pricing rule, a payment processor call — and the caller shouldn't need to change when a new one is added or the active one is swapped, whether that swap happens at startup (config-driven) or per request (per-merchant, per-tenant).

## How It Works

The caller depends on one interface describing the operation ("charge this amount," "compute this fee"), never on a concrete implementation. Each interchangeable algorithm is its own class implementing that interface. Which concrete instance the caller actually holds is decided elsewhere (often by a [Factory Method](03-factory-method.md)) — the caller itself only ever sees the interface, so adding a new algorithm never requires changing the caller.

## Implementation

```
interface ProcessorClient:
    charge(amount, currency, method, idempotencyKey) -> ChargeResult

class OrchestrationService:
    processor: ProcessorClient       # depends on the interface, never a concrete class

    charge(amount, currency, method, idempotencyKey):
        return self.processor.charge(amount, currency, method, idempotencyKey)
```

Adding a third processor (a new class implementing `ProcessorClient`) is zero changes to `OrchestrationService` — the only thing that grows is which concrete class gets constructed and handed in.

## Real-World Use Case

This is already used constantly across this guide — the [URL Shortener's `KeyGenerator`](../../../02-lld-fundamentals.md) (base62-from-counter is one strategy; hash-and-truncate is a valid alternative behind the same interface) and this guide's [Distributed Rate Limiter](../../case-studies/distributed-rate-limiter/02-lld.md) (`TokenBucketRateLimiter`/`SlidingWindowRateLimiter`, interchangeable behind one `RateLimiter` interface) are both this pattern.

## When to Use It

- Two or more implementations of the same operation genuinely exist, or are a realistic near-term need.
- The active implementation might need to change at runtime (per merchant, per tenant, per config value) without redeploying the caller.
- You want to unit-test the caller against a fake implementation of the interface, independent of any real one.

## When NOT to Use It

If there's genuinely only ever going to be one algorithm, with no swapping and no realistic second implementation, the interface is pure ceremony — call the one implementation directly and remove the indirection.

## Related Patterns

Worth naming the distinction from [Factory Method](03-factory-method.md) explicitly, since both often involve the exact same interface: Factory Method decides *which* concrete implementation to construct in the first place; Strategy is about the interface itself being swappable at the call site, regardless of how the concrete instance behind it got there. They compose naturally — a factory constructs the right strategy for a given input — but they answer different questions.
