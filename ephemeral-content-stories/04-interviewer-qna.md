# Module 04 — Interviewer Follow-Up Bank

Questions an interviewer would plausibly ask about this specific design, each answered from a decision already made in modules 01–03 — not generically.

**1. What happens when two requests hit the same story/viewer pair at the same instant?**
Two `recordView` calls for the same `(story_id, viewer_id)` race at the database's unique constraint (Module 03) — the second is a no-op via `insertIfAbsent` (Module 02). The live view count is a Redis `SADD` into a per-story Set rather than a raw counter, so even if both requests got that far, the count still can't double-increment. The loser sees nothing; nothing is wrong from their perspective either.

**2. What happens when traffic spikes 10x for an hour — a major event everyone's reacting to?**
Reads (viewing) are prioritized over writes (posting) under pressure: new uploads get a 429 with retry-after before any read is throttled (Module 01, Load Handling), because the availability requirement explicitly values "keep serving what exists" over "keep accepting what's new." Autoscaling reacts in 1–2 minutes; in the gap, Redis and the connection pool absorb load, and a saturated Redis degrades reads to the MySQL replica rather than failing them.

**3. Why Redis TTL instead of a cron job for expiry?**
A cron job scanning/deleting from a billion-row-a-day table is both slow (huge write amplification) and a correctness risk — if it runs late, expired content stays visible, which the requirements explicitly forbid. Redis key expiry is a single atomic operation with no batch process involved, so there's no "late" state to worry about.

**4. What if Redis goes down entirely — not just slow, but unreachable?**
`getTray` falls back to checking `expires_at` on the SQL row directly (Module 02's sequence diagram shows this branch explicitly) — slower, but still correct, because that column exists for exactly this scenario (Module 03). `create` retries `markVisible` and rolls back rather than leaving a story ungated if Redis stays down through the retry budget — an ungated story is worse than a failed post.

**5. This design uses fan-out-on-read for the Story Tray. Why not fan-out-on-write, like a lot of news feed systems use?**
Because the visibility set for a story is *your own* following list — small, bounded, and mostly stable — not your follower list, which is what makes fan-out-on-write attractive (and necessary) for feed problems with celebrity-scale followings. Pushing a new story into every follower's precomputed tray on every post would write-amplify for no benefit here.

**6. How do you avoid double-counting "a billion views a day"?**
Same mechanism as question 1: the unique constraint on `story_views(story_id, viewer_id)` plus a Redis Set (not a counter) for the live count. A raw `INCR` would double-count on any retry or duplicate delivery; `SADD` followed by `SCARD` is naturally idempotent, because adding the same member to a set twice has no effect.

**7. A user requests account deletion (GDPR/right-to-erasure). What has to happen?**
Their still-live stories disappear on their own within 24 hours regardless, but that's not enough — `story_views` retains data for the ~30-day audit window (Module 01's math), including views *of* their content by others and views *by* them of others' content, both of which reference `user_id`. A deletion job needs to anonymize or remove their rows in both `stories` and `story_views` within the retention window, independent of the normal partition-drop cleanup cycle (which runs on a schedule, not on-demand).

**8. Media storage sounds enormous — 4PB/day gross. How would you bring that down?**
Three levers, all already implied by the design: more aggressive video compression before the "original" is even stored (a UX/quality trade-off, not a free lunch), dropping the original once the transcoded variant is confirmed good rather than keeping both indefinitely, and shortening the cold-storage retention window from 30 days if the abuse-review use case allows it — each trades some capability (quality, rollback, review time) for cost, and the answer is "depends what the business needs," not a single fix.

**9. Argue the other side: why might DynamoDB be a better fit than MySQL here after all?**
DynamoDB has native item TTL, which could arguably replace the Redis gate *and* the partition-drop cleanup with one mechanism instead of two, and it scales writes horizontally without the sharding-by-hand this design does manually. The counter-argument (Module 03) is that DynamoDB's TTL sweep isn't instant either (real-world lag is close to 48 hours), so you'd still need Redis as the correctness-critical gate anyway — at which point DynamoDB's main advantage over partitioned MySQL narrows to operational convenience, not a capability MySQL lacks.

**10. A node holding a shard's primary dies mid-write. What happens to a `create()` in flight?**
The in-flight write fails (the client sees an error and can retry — `create` isn't idempotent by story ID, so a naive client retry could create a duplicate story from one upload; a client-generated idempotency key on the create request, deduplicated the same way `recordView` is, is the fix worth naming here even though the base design doesn't require it at this scale). Once a replica is promoted, later requests to that shard succeed again; nothing about the visibility gate is affected, since Redis is a separate system from MySQL's failover.
