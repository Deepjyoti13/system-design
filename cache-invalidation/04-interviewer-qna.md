# Module 04 — Interviewer Follow-Up Bank

Ten questions a real interviewer would push on after seeing modules 00–03, each answered from a decision already made rather than a generic textbook answer.

**1. What happens when two requests hit the same resource at the same instant?**
The concrete version: a cache-miss read is about to write a stale value back into the cache exactly as an invalidation for that same key arrives. Every cached entry and every invalidation event carries a version; the repopulation write is compared against the last-seen invalidation watermark before it's applied, and a stale write loses — silently discarded, not an error. The next read simply misses again and picks up the current value.

**2. What happens when traffic spikes 10x for an hour?**
Read traffic scaling 10x is a normal cache-serving problem, not an invalidation one — invalidation volume doesn't automatically scale with read volume. The case this design is actually built for is a *write-side* burst: a mass re-price or a forced cache bust producing tens of thousands of invalidation events in seconds, handled by Kafka's own buffering plus the Purge Coordinator's self-imposed rate limit against the CDN.

**3. Why version the cache key at all — why not just always actively purge?**
Active purge means broadcasting an event and waiting for every layer (including a CDN with real propagation delay and rate limits) to acknowledge it — a race by construction. A versioned key sidesteps the race entirely: the new content simply lives under a new key, so old copies are never "wrong," they're just never looked up again. Active purge is kept only for the cases — like a fixed logical key such as "this user's session" — that have nowhere to put a version.

**4. What happens if the CDN's purge API is down?**
The circuit breaker around the Purge Coordinator's CDN calls opens, and purges queue rather than retry-storming a provider that's already failing. Nothing about the source-of-truth read/write path is affected — a stuck CDN purge queue is a slower-than-usual cache convergence, not an outage, which is exactly the availability requirement module 01 states.

**5. How do you avoid a thundering herd when a hot key is invalidated and thousands of readers miss at once?**
This is the same stampede problem [Caching Strategies](../content/hld-building-blocks/caching-strategies.md) already names — request coalescing (the first miss fetches and populates; concurrent misses for the same key wait on that one fetch instead of each hitting the DB) applies here identically; invalidation doesn't change the mitigation, it just changes what triggers the miss.

**6. How would you invalidate an entire category of keys at once (a wildcard purge), not just one key?**
Named explicitly in module 02 as a real gap: the current `onInvalidate(key, version)` shape assumes one specific key. A wildcard purge either needs the versioning to move up a level (version the *category*, not each product, so a category bump cascades) or a genuinely different mechanism for that case — not something this design solves as written.

**7. Why does the app-server local cache need to subscribe to the same event bus instead of just using a short TTL?**
With 200+ instances on TTL alone, different users can hit different app servers and see visibly different answers for up to a full TTL window after a change — worse than a slow cache, an *inconsistent* one. The event bus gets every instance the same message in the same fan-out, which is what keeps "which server did I happen to hit" from mattering.

**8. What's the actual cost/complexity driver here, and how would you reduce it?**
The CDN purge path — rate-limited, slow to propagate, needing its own retry and tracking logic — is the expensive, fragile part. Pushing as much content as possible onto versioned keys (module 01's stated preference) directly shrinks how much traffic that fragile path ever has to carry.

**9. How would you test that invalidation actually works before relying on it in production?**
Against the load-handling target from module 01: fire a burst of 20,000 invalidation events within 5 seconds and confirm 99% of app-server-local caches update within 500ms, with CDN purges queued (not dropped) and fully drained within 60 seconds — then repeat with the Kafka topic or the Purge Coordinator briefly unavailable to confirm the graceful-degradation path holds, not just the happy path.

**10. Would you build this whole pipeline for a small app with one cache in front of one database?**
No — the monolith-vs-microservices section says this directly: a single-cache system doesn't have multiple independently-stale layers to reconcile, so there's nothing for an event bus or a Purge Coordinator to coordinate. A short TTL is the entire correct answer at that scale; this pipeline earns its cost only once there are genuinely multiple, independently-living copies to chase down.
