# Module 02 — Low-Level Design

**Diagram for this module:** [`diagrams/02-sequence.svg`](diagrams/02-sequence.svg)

## The two components worth opening up

Everything else in module 01 is "publish an event" or "call a datastore." Two pieces have a real decision inside them: the **invalidation listener** at each cache layer (specifically, how it avoids the stale-repopulation race) and the **versioned-key builder** (how a logical key becomes a version-scoped cache key).

## Interfaces vs. implementations

- **`InvalidationListener`** *(interface)* → **`LocalCacheInvalidationListener`**, **`RedisInvalidationListener`** — `onInvalidate(key, version)`.
- **`CacheEntry`** — carries `(value, version)`, not just `value`. The version is what the repopulation race is actually resolved against.
- **`VersionedKeyBuilder`** *(interface)* → **`ContentHashKeyBuilder`** — `cacheKey(logicalKey, version)`, e.g. `product:123:v7`.
- **`InvalidationWatermark`** — a small per-key store of "the newest version we've been told about," kept even briefly after the entry itself is evicted, specifically so a late repopulation write has something to compare against.

Naming the patterns: `InvalidationListener` implementations are the same **Strategy** shape this project's other worked examples use for a swappable per-layer behavior. `CacheEntry` carrying its own version alongside its value is what makes the compare-before-write below possible without any cross-instance coordination.

## Pseudocode for the two operations that matter

```
CacheLayer.get(key):
    entry = localStore.get(key)
    if entry:
        return entry.value

    (value, version) = sourceOfTruth.fetch(key)     # DB read; the row's own version comes back with it
    localStore.putIfNewer(key, value, version)       # no-op if a newer version is already on record
    return value

CacheLayer.putIfNewer(key, value, version):
    watermark = invalidationWatermark.get(key)
    if watermark is not null and version < watermark:
        return                                        # strictly stale — discarded, not applied
                                                        # (version == watermark is a fresh re-fetch
                                                        # that matches current truth: allowed through)
    localStore.set(key, value, version)

InvalidationListener.onInvalidate(key, newVersion):
    invalidationWatermark.set(key, newVersion)         # bump the watermark first
    localStore.evictIfOlder(key, newVersion)           # then drop any entry the new version supersedes
```

Two error cases worth designing for deliberately:

- **A repopulation write loses the race to an invalidation:** `putIfNewer` returning without writing is not an error — it's the entire mechanism working as intended. The caller gets back the value it fetched (still correct to *return* to this one request), but the cache itself doesn't retain it, so the next read is guaranteed to try again rather than serving a value the system already knows is stale.
- **The watermark itself is evicted or never set:** a key that's never been invalidated has no watermark yet, and `putIfNewer` treats "no watermark" as "anything is newer" — the very first write always wins. This has to be the explicit default, not an accident of a null check, or every cold key would silently reject its first write.

## Concurrency, at the code level

`putIfNewer`'s compare-then-write needs to be atomic per key, but it does **not** need cross-instance coordination — each app server's local cache only has to be consistent with itself; Redis's version does the same compare using a single atomic operation (a Lua script, or a conditional `SET` against a stored version field) rather than an application-level lock, for the same reason this project's other hot-write designs push atomicity down into the store instead of the caller: an in-process mutex on one instance would do nothing to protect the other 199 instances also racing the same invalidation.

## Practice: extend it yourself

Before moving to module 03, sketch how you'd add:

1. **A wildcard purge** ("invalidate every cached page under `/category/shoes`") — does this fit `InvalidationListener.onInvalidate(key, version)` as written, or does the interface need to change to accept a pattern instead of one key?
2. **A "soft" invalidation** that marks a value stale but still serves it briefly (stale-while-revalidate) instead of evicting outright — where does that logic belong: inside `CacheLayer.get`, or as a different `CacheEntry` state alongside the version?

Neither has one right answer — the point is noticing where the existing interface boundaries make the extension obvious, and where they don't.
