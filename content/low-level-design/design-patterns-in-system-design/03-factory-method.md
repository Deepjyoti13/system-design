# Factory Method

![Factory Method: ProcessorClientFactory deciding which concrete ProcessorClient to build from a providerType flag](diagrams/factory-method.svg)

## The Problem It Solves

Code needs to decide *which concrete class to instantiate* based on a runtime value — a config flag, a merchant's chosen provider, a document's file type — and that decision, if inlined as an `if/else` or `switch` at every call site that needs a new instance, gets duplicated across the codebase and drifts out of sync the moment a third option is added somewhere and forgotten elsewhere.

## How It Works

One method, in one place, owns the entire "which concrete class" decision. It takes whatever runtime value drives the choice, and returns an object typed as the shared interface — never the concrete class name. Every caller of the factory depends only on that interface from then on; the concrete type becomes an implementation detail the factory alone knows about.

## Implementation

```
class ProcessorClientFactory:
    create(providerType) -> ProcessorClient:
        if providerType == "STRIPE":
            return StripeProcessorClient(stripeConfig)
        if providerType == "ADYEN":
            return AdyenProcessorClient(adyenConfig)
        raise UnknownProviderException(providerType)

# OrchestrationService never sees a concrete class name:
class OrchestrationService:
    processorFactory: ProcessorClientFactory

    charge(merchant, amount, ...):
        client = self.processorFactory.create(merchant.providerType)   # a plain ProcessorClient from here on
        return client.charge(amount, ...)
```

## Real-World Use Case

This is exactly what a `ProcessorClientFactory` in front of this guide's [Payments System](../../case-studies/payments-system/02-lld.md) would do — decide, from a merchant's configured `providerType`, whether to construct a `StripeProcessorClient` or an `AdyenProcessorClient`. Adding a third provider later means one new `if` branch inside `ProcessorClientFactory.create()` and nowhere else — `OrchestrationService` doesn't change at all.

## When to Use It

- The concrete type to construct genuinely depends on a runtime value, not something known at compile time.
- A third (or fourth) option is a realistic future possibility, and you want that growth to touch exactly one place.
- Callers should depend only on the shared interface, never on a specific concrete class name.

## When NOT to Use It

If there's only ever going to be one concrete implementation, with no realistic second one on the horizon, a factory is a layer of indirection standing in for a plain constructor call — just construct the one class directly and skip the factory entirely.

## Related Patterns

Worth naming the distinction from Strategy explicitly, since both often involve the exact same interface: **Factory Method decides which concrete implementation to construct in the first place**; **Strategy is about the interface itself being swappable at the call site**, regardless of how the concrete instance behind it got there. They compose naturally — a factory constructs the right strategy for a given input — but they answer different questions. See the [Strategy](10-strategy.md) page for the other half of that distinction.
