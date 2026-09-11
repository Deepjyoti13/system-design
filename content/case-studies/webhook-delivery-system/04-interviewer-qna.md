# Module 04 — Interviewer Q&A

**1. What happens when two delivery workers race to claim the same due delivery at the same instant?**
The claim query's own `WHERE` clause -- `status='pending' AND next_attempt_at<=now()` plus the sequence-gate's `NOT EXISTS` -- is evaluated as one atomic conditional `UPDATE`, so only the first worker's statement actually matches the row; the second worker's identical claim affects zero rows and moves on to the next delivery in its batch, the same database-enforced discipline this guide uses for payments' idempotency-key insert and the job scheduler's due-jobs claim.

**2. What happens when a subscriber's outage clears and thousands of its retries all become due within the same second?**
This is the actual load case this design is built around, not raw traffic volume: exponential backoff with full jitter means each of those retries computed a slightly different wait even though they all failed during the same window, so recovery is smoothed across several seconds instead of hitting the worker pool's claim query as one synchronized spike -- see Module 01's Load Handling and the load-test target built specifically around this scenario.

**3. How do you know a subscriber didn't already receive and process an event you're about to retry?**
You don't, and unlike this guide's [payments case study](../payments-system/00-overview.md) -- which can query the processor's own status API to resolve an ambiguous timeout -- there's no equivalent recourse here, because the subscriber's server is a black box this platform doesn't operate. The deliberate choice is to retry anyway and lean entirely on the subscriber's own idempotent handling of the `Webhook-Id` header; at-least-once with a harmless duplicate is strictly better than risking a silently dropped event.

**4. Does a subscriber ever see event B before event A, if A is still retrying?**
No -- this design takes a real position on it: one in-flight attempt per subscription, and the claim query's ordering gate keeps sequence N+1 out of the claimable set entirely until sequence N reaches a terminal state (succeeded or dead-lettered). The cost is real and scoped precisely: A's own retry sequence can hold B up for A's full backoff schedule (up to ~72 hours), but that delay is contained to this one subscriber's queue -- it has zero effect on any other subscriber's delivery latency. Stripe's own documented position is the opposite trade -- no ordering guarantee, subscribers reorder using an event's own timestamp -- and it's a legitimate alternative; this design bets that centralizing the guarantee once beats asking every third-party integrator to implement reordering correctly.

**5. Would you ever accept eventual consistency on the claim and ordering-gate path?**
No -- same CP-leaning reasoning this guide's payments case study and distributed job scheduler both give for their own claim paths (cross-ref [Latency, Throughput & CAP](../../foundations/latency-throughput-cap.md)): a stale read of "is the prior sequence terminal" doesn't just risk duplicate work here, it risks silently breaking the one guarantee -- per-subscriber ordering -- this whole design exists to provide.

**6. What happens if the entire delivery worker pool goes down for an hour?**
Nothing is lost -- fan-out already durably committed one `deliveries` row per `(event, subscription)` before any HTTP attempt was ever made, so an hour of worker downtime just means every due delivery waits an extra hour before its next claim. Subscribers see webhooks arrive late once the pool recovers, never missing -- the same graceful-degradation shape payments' webhook relay has, except here it's this system's entire job rather than a side effect of another one.

**7. Why fan out into one row per subscriber at ingestion, instead of resolving subscriptions at delivery time?**
Because retry state, attempt history, and circuit-breaker health are facts about one specific subscriber's relationship to one specific event -- joining subscriptions in at delivery time would mean a slow subscriber's repeated lookups compete with, and can't be cleanly separated from, a fast subscriber's single successful one. Fanning out once, up front, is what makes "isolate subscriber B's failure from subscriber A" a property of the schema itself rather than something application code has to enforce on every read.

**8. How would you prevent one very high-volume tenant from starving smaller tenants' delivery latency?**
Honestly -- this design doesn't solve that yet, and it's named explicitly in Module 01's "what you'd revisit" as the same kind of hot-shard fairness gap the distributed job scheduler names for its own shared-claim model. A mature version would need per-tenant rate limiting or weighted claim priority, layered on top of, not replacing, the per-subscription circuit breaker this module does build.

**9. Why HMAC-signed payloads instead of requiring mutual TLS from every subscriber?**
A shared secret is something a subscriber stores once and verifies against on every request; mutual TLS would require every third-party integrator to provision, rotate, and correctly validate a client certificate just to receive a webhook -- a much higher integration bar for the exact same guarantee. It's also not hypothetical: Stripe, GitHub, and Shopify all ship the HMAC-header version of this, not mTLS, for their own webhook products.

**10. Why isn't the webhook call made synchronously, inside the producing service's own request?**
Because the receiving endpoint is untrusted, third-party infrastructure this platform doesn't control -- coupling a producer's request latency (or success) to a subscriber's uptime is exactly the coupling the [transactional outbox](../../hld-building-blocks/transactional-outbox-cdc.md) and async-relay pattern exist to break. Payments' own webhook relay makes this same call for a handful of payment-lifecycle events; this system generalizes it to the platform's entire event catalog, which is precisely why the retry/backoff/circuit-breaker/ordering machinery has to be this module's actual subject, not a side path bolted onto something else.
