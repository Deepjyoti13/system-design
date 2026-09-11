# Module 04 — Interviewer Follow-Up Bank

**What happens when two requests hit the same resource at the same instant?**
The only real "resource" here is a flag ROW during an edit — two engineers saving the same flag's rollout percentage within the same second. The optimistic-concurrency check (`WHERE version = ?`, module 03) means the second writer's save fails outright with "this changed since you loaded it," rather than silently overwriting the first edit. On the READ/evaluation side there is no equivalent race at all: two requests evaluating the same flag for the same user, at the same instant, on the same or different instances, independently compute the identical deterministic hash and land on the identical answer — there's nothing to contend over.

**What happens when traffic spikes 10x for an hour?**
Nothing happens to this feature's own systems, and that's the point: evaluation is a local function call inside whatever service is experiencing the spike, so it scales exactly as far as that service's own existing capacity does, with zero additional load on the Flag Config Service, the Config Store, or the distribution pipeline. A 10x spike in flag EDITS (a scripting bug hammering the admin API) is a different, much smaller-scale problem, and would be handled with ordinary [rate limiting](../content/hld-building-blocks/rate-limiting.md) on the control plane.

**Why not just cache flag values in something like Redis instead of building an embedded, in-process SDK?**
A remote cache still costs a network round-trip per lookup — at 200M evaluations/sec, that's 200M/sec of Redis traffic, not zero. It would also introduce a shared point of contention across every instance, exactly what the embedded-snapshot design avoids by giving each instance its own local, independently-swappable copy.

**How would you support a flag that depends on ANOTHER flag's state ("only show B if A is on")?**
As a targeting rule that references another flag's evaluated result rather than raw user attributes — the evaluator would need to resolve dependencies in order (evaluate A before B), which is a small addition to the rule-matching loop in module 02, not a change to the overall architecture.

**How would you roll a flag back instantly if the new version breaks something?**
The same way a forward change ships: publish a new version. Because every instance already treats "swap to whatever version the notification channel points at" as the one and only update path, a rollback is not a special case — it's just another config write pointing at the previous (or a corrected) snapshot, propagating through the identical pub/sub-plus-CDN path in the same few seconds as any other change.

**What's the actual blast radius if the Config Store goes down for an hour?**
Zero impact on evaluation (every instance is already serving its last-known-good in-memory snapshot, per module 01's explicit "freeze, don't fail open or closed" failure mode) and a full stop on flag EDITS for that hour — a real but bounded degradation, since the thing that can't happen (new rollouts, kill switches) is far less damaging than the thing that's protected (every other feature these flags gate continuing to work normally).

**Why does the rollout use a hash of `(flag key, user ID)` instead of storing each user's assigned bucket in a table?**
Storing an assignment row would mean a database write (or at least a lookup) for the FIRST time every user hits every flag — reintroducing exactly the per-evaluation I/O this entire design exists to eliminate. The deterministic hash gets the identical property (same user, same bucket, forever) for free, computed locally, with zero storage.
