# Module 04 — Interviewer Q&A

**1. Why not just call a central denylist service over the network for every check?**
At the assumed scale — over a million checks/sec company-wide — that call would make the denylist service the busiest, most fragile dependency in the entire company, and every caller's own latency would now include a network round trip on every single request. A local, replicated Bloom filter answers the overwhelming majority of checks with zero network cost, which is the entire reason this system's shape is "push the check to the edge," not "centralize the check."

**2. Bloom filters can return false positives — isn't that a correctness bug?**
No, and this is the central design insight: a false positive here means occasionally double-checking something that turns out fine — cheap, and invisible to the end user beyond a few extra milliseconds. A false negative would be a real security miss, and a Bloom filter's own math guarantees it can never produce one (if an entry was added, `mightContain` always returns true for it). The asymmetry between "cheap to over-check" and "dangerous to under-check" is exactly what makes a Bloom filter the right structure here, not just a memory optimization.

**3. How does a newly-added malicious entry actually reach every service checking against it?**
Through a push-based replication pipeline — new entries are published to a message bus, and every local filter (or a periodic snapshot refresh) picks them up on a bounded delay. This guide names that delay as a real, stated freshness SLA rather than assuming propagation is instantaneous — a newly-added entry is genuinely, briefly still allowed everywhere until replication catches up.

**4. What happens if the authoritative store is completely unreachable?**
Every local filter keeps serving from whatever it last successfully loaded — checks keep working, just against a filter that stops getting fresher. The only path that's actually affected is the rare authoritative-confirm call on a possible match, which is wrapped in a circuit breaker and falls back to each calling service's own configured fail-open or fail-closed policy rather than blocking indefinitely.

**5. Why would some callers fail open and others fail closed on a denylist-check failure?**
Because the cost of each failure mode is completely different depending on what's being protected. A low-stakes internal analytics call failing open (allowing the request through) during an outage is a reasonable trade; a payment-authorization call failing open could let something genuinely dangerous through. A single global policy would force every caller to accept whichever trade-off doesn't actually fit their own risk profile.

**6. How do you rebuild or rotate a local Bloom filter without a gap in coverage while it's rebuilding?**
The new filter is built entirely in the background from a fresh snapshot — the currently-active filter keeps serving every check throughout that process — and only once the new filter is fully populated does an atomic reference swap make it live. There's never a moment where checks run against a partially-populated filter, because the old, complete filter is what's serving right up until the swap.

**7. Two different threat-intel feeds report the same malicious IP around the same time — what happens?**
The authoritative store's ingestion write is idempotent, keyed by the entry's own value — the second write is a no-op rather than a duplicate row. Whichever write actually lands first is what the entry's metadata (first-seen time, source) reflects; this is the same idempotent-write discipline this guide applies anywhere two independent sources might report the same fact.

**8. Would you ever remove an entry from the Bloom filter directly, for a false-positive correction?**
No — a standard Bloom filter has no removal operation; clearing bits for one entry can silently break the guarantee for a different entry that happens to share those bits. A correction has to be modeled as its own explicit mechanism (a small, separately-checked "definitely allow" override list, or waiting for the next full filter rebuild from a corrected authoritative store), never as an in-place edit to the filter's bit array.

**9. How would you extend this design to support entries that should expire automatically after a fixed TTL?**
The TTL has to live in the authoritative store's schema (an `expires_at` on each entry) and be respected by whatever generates a snapshot for filter rebuilds — an expired entry simply isn't included in the next snapshot a local filter builds from. The Bloom filter itself has no concept of expiration; a full periodic rebuild (rather than only ever adding via deltas) is what actually lets expired entries eventually stop being blocked.

**10. Why replicate a Bloom filter to every caller instead of just caching individual "not blocked" answers per entry as they're checked?**
A per-entry cache only helps for entries that have already been checked at least once — the first check for any given entry would still need to hit the authoritative store, and an attacker's IPs are, by definition, mostly ones nobody has checked before. A Bloom filter covering the *entire* denylist up front means even the very first check against a brand-new, never-seen entry is answered locally, with no cold-start gap.
