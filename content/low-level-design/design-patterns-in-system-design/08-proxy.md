# Proxy

![Proxy: CachingProxy and the real store behind the same interface, the caller unable to tell which one answered](diagrams/proxy.svg)

## The Problem It Solves

You need to control or defer access to a real object, without the caller changing how it calls it. Three common flavors of the same shape: a **caching proxy** answers from a cache before ever reaching the real object; a **virtual proxy** defers an expensive real object's construction until it's actually needed; a **protection proxy** checks permissions before delegating. All three implement the same interface as the real object and decide, per call, whether to actually reach it.

## How It Works

The proxy implements the exact same interface as the real object and holds a reference to it. On each call, the proxy first decides — based on a cache, a permission check, or whether the real object has even been constructed yet — whether to actually delegate to the real object or answer some other way. The caller depends only on the shared interface and structurally cannot tell which path a given call took.

## Implementation

```
interface DataStore:
    get(key) -> Value

class RealDatabase implements DataStore:
    get(key):
        return runQuery(key)                     # a real, slow round trip

class CachingProxy implements DataStore:
    real: DataStore
    cache: Cache

    get(key):
        cached = cache.get(key)
        if cached is not None:
            return cached                        # real is never touched on a hit
        value = real.get(key)
        cache.set(key, value, ttl = 60)
        return value                              # caller can't tell this came from a miss
```

## Real-World Use Case

This is the exact method a cache-aside layer implements — this guide's [Caching Strategies](../../hld-building-blocks/caching-strategies.md) page names the pattern by its caching behavior; here it's named by its structural shape. A [CDN](../../hld-building-blocks/cdn.md) is the same pattern operating at global edge scale: every edge PoP is a caching proxy sitting in front of an origin the client never addresses directly.

## When to Use It

- You want to add caching, lazy construction, or an access check in front of a real object, without the caller's code changing at all.
- The caller must not be able to tell — and shouldn't need to care — whether a given call reached the real object or was intercepted.
- The check (cache lookup, permission check, construction cost) genuinely varies per call, not just once at startup.

## When NOT to Use It

If the caller is fine calling the cache and the real store as two separate, explicit steps, forcing both behind one interface adds a layer with no behavior payoff — Proxy earns its place specifically when the caller must not be able to tell the difference.

## Related Patterns

Worth distinguishing a caching proxy from a protection proxy explicitly: same structural shape (implements the real object's interface, decides whether to delegate), but the decision is "is this caller allowed," not "do I already have this cached" — different question, identical class diagram. Proxy is also structurally close to [Decorator](06-decorator.md): the difference is that a decorator's wrapped call always happens (it only adds behavior around it), while a proxy's wrapped call sometimes doesn't happen at all.
