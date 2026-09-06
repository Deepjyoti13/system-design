# Module 04 — Interviewer Follow-Up Bank

**What happens when two requests hit the same resource at the same instant?**
Two concurrent charge attempts on the same card increment the *same* velocity counter atomically (module 02's `incrementAndGet`) — there's no read-then-write gap for them to race inside. Whichever request's increment lands second sees the true post-increment count, including the first request's contribution, so a burst of 3 rapid attempts is correctly seen as 3, not three separate views of "1."

**What happens when traffic spikes 10x for an hour?**
The Scoring Service is stateless and scales horizontally the same as the Payment Orchestration Service it sits beside; the real ceiling is the Velocity Counter Store's write throughput, which scales by sharding counters across nodes by `card_id` (module 03). If the counter store itself starts saturating, module 01's fail-open fallback (a smaller, static rule set) keeps decisions flowing within budget rather than the whole payment path backing up behind a slow fraud check.

**Why fail open (allow) rather than fail closed (block) when the Scoring Service is down?**
Because both directions are expensive, but they're not *equally* expensive at the margin: blocking every payment during a scoring outage stops 100% of legitimate revenue to prevent a fraction-of-a-percent fraud rate from ticking up temporarily for a few minutes. Fail-open with a conservative fallback rule set (not "approve everything blindly") is the practical middle ground — cross-ref [Latency, Throughput & the CAP Theorem](../content/foundations/latency-throughput-cap.md)'s framing of this as an availability-favoring choice made deliberately, not by default.

**How would you decide the score threshold that separates auto-approve from manual review?**
Not by guessing — by tuning against labeled historical data (confirmed fraud vs. confirmed legitimate transactions) to hit a target false-positive rate the business can tolerate, then monitoring the *actual* decline rate and chargeback rate in production and adjusting. A threshold chosen once and never revisited drifts wrong as fraud patterns change, which is exactly the model-drift risk module 01's "what you'd revisit" section names.

**Could you use this same architecture for a non-payments real-time risk decision, like account-takeover detection at login?**
Yes — the shape (a warm feature store, an atomic velocity signal, a rules/model scoring step, a tiered outcome) is generic to "score something in real time before letting an action proceed." What changes is the specific features (login velocity and device/IP reputation instead of card velocity) and what each tier means (challenge with 2FA instead of manual review), not the underlying architecture.

**Why is the velocity counter kept in Redis instead of just adding an index to the payments database and counting rows?**
Because "count recent rows matching this card" run against a live, growing transactions table on every single payment would itself become the latency problem this whole module exists to prevent — an index helps a query be less slow, it doesn't make a full aggregate query as fast as reading one pre-maintained integer. Module 03 makes this trade explicit: durability and queryability (the payments DB) versus raw atomic-increment speed (Redis) are different requirements best served by different stores.

**What happens to a transaction that's scored while its OWN previous attempt is still `pending` in the payments system?**
The fraud check and the payment's own idempotency handling are separate concerns operating on separate keys — the fraud check scores this specific transaction attempt on its own merits (including that repeated-attempt velocity signal), while [Payments System](../content/case-studies/payments-system/01-architecture-hld.md)'s idempotency-key mechanism separately ensures a retried request with the *same* key doesn't get double-charged regardless of what the fraud check decides.

**How would you test that the fail-open fallback path actually works, before you need it in production?**
The same way this guide's [Database Replication & Failover](../content/database-design/db-replication-failover.md) module answers the analogous question for a database failover: deliberately trigger it — force a Scoring Service timeout in a staging environment and confirm the payment path falls back to the static rule set and keeps completing within budget, rather than assuming the untested code path works the day it's actually needed.
