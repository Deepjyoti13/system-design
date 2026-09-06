# Module 04 — Interviewer Q&A

**What happens when two requests hit the same resource at the same instant?**
Two scheduler processes racing to claim the same due job — whether from a brief split-brain during leader failover or any other overlap — resolve at the atomic claim: `UPDATE ... WHERE status='due'` only ever succeeds for the first claimant, because the row's current status is part of the update's own condition, not checked separately beforehand.

**What happens when traffic spikes 10x for an hour?**
This system's real spike shape is a burst of simultaneously-due jobs (a round-number-time cluster), not sustained load — bounded-batch claiming caps how many jobs any one scheduler tick dispatches, and the trigger queue absorbs whatever the worker pool can't immediately execute, so the burst is drained over a few extra seconds rather than triggering a worker-pool overload.

**How would you handle clock skew between scheduler instances?**
Rely on the database's own clock for "is this job due" (the `next_run_time <= NOW()` comparison happens inside the job store's query, using one authoritative clock) rather than trusting each scheduler instance's local clock — this sidesteps skew between scheduler processes entirely, since none of them are the source of truth for "now."

**How would you guarantee exactly-once triggering, given the claim is only atomic, not distributed-transactional with the trigger queue publish?**
Full exactly-once here would need the claim and the publish to be one atomic unit spanning two systems, which this guide's [Transactional Outbox](../../hld-building-blocks/transactional-outbox-cdc.md) pattern solves for exactly this shape of problem: write the "trigger this job" event into an outbox row in the SAME transaction as the claim, and let a relay publish it — turning "exactly-once claim, best-effort publish" into "exactly-once claim AND publish," at the cost of the outbox's own relay delay.

**Would you use a distributed lock instead of leader election for each shard?**
They're solving related but different problems: leader election designates one long-lived owner for an entire shard's ongoing scanning work; a distributed lock (cross-ref [Distributed Locks](../../scalability-resilience/distributed-locks.md)) is better suited to a single, short operation. Using a lock for continuous shard ownership would mean constantly re-acquiring it, which is exactly what a lease-based leader election already manages more cheaply.

**How would you support a job that must run on a SPECIFIC worker (not any available one)?**
Add a routing key to the trigger event and have the worker pool's consumers filter or partition by it (cross-ref [Message Queues & Pub/Sub](../../hld-building-blocks/message-queues-pubsub.md)'s point about partition keys controlling which consumer sees which message) rather than changing anything about how the scheduler claims and dispatches — the scheduler doesn't need to know or care which specific worker eventually executes a job.

**What happens if a job's execution takes longer than its own scheduled interval (a job scheduled every minute that takes 90 seconds to run)?**
This is a worker-pool concurrency policy decision, not a scheduler decision: either allow overlapping runs of the same job (if genuinely safe) or explicitly skip/queue the next trigger until the current run finishes — but the SCHEDULER's job of noticing "due" and dispatching stays the same either way; the overlap policy lives with whichever component actually executes the job.

**How would you let a user retroactively change a recurring job's schedule?**
Update the job definition's `cron_expr` and recompute `next_run_time` from it — since the scheduler always reads `next_run_time` fresh from the job store on every tick rather than caching a schedule in memory, a schedule change takes effect on the very next tick without any special invalidation logic.
