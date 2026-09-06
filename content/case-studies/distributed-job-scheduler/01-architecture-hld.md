# Module 01 — Architecture & High-Level Design

![A leader-elected scheduler shard claiming due jobs atomically before handing them to a worker pool](diagrams/hld.svg)

## Monolith vs. microservices

The scheduler tier is pulled out as its own service, never embedded inside each job-owning service (billing, email, reporting, whatever actually needs something to happen later). The reason is that leader election per shard has to be a single, shared source of truth — if every service that wanted scheduled work ran its own embedded scanning loop, you'd need a separate leader-election mechanism per service, no shared way to reason about "has this exact job already been triggered," and no single place to reason about shard rebalancing as job volume grows. Centralizing scheduling means there's exactly one component in the whole platform whose job is "notice it's due and hand it off exactly once," and every other service just calls its API.

There's a second, independent reason the seam holds: this tier's operational profile is fundamentally different from a request-response service. A scheduler is *always* doing work — continuously scanning for due jobs — rather than idle between requests. Folding that into a monolith would mean the background scan competes for the same capacity as the monolith's user-facing latency budget, and a slow scan under load would show up as slower request handling for something that has nothing to do with scheduling at all.

## Per-path walkthrough

**Claim-and-dispatch path (write)** — `Shard leader (tick fires) → Job Store (query due jobs, bounded batch) → Job Store (atomic conditional claim, one row at a time) → Trigger Queue (publish, idempotency_key = job_id + scheduled time) → Worker Pool (consume, execute, retry on failure) → Run History Store (record outcome)`. The claim is the one step in this whole path that has to be airtight; everything downstream of it (publish, execute, record) can be retried safely because the claim itself is what turns "maybe due" into "definitely, exactly-once, mine to trigger."

**Missed-schedule catch-up path** — `Scheduler process down while a job was due → job's next_run_time stays in the past, untouched → a leader resumes scanning (after restart or failover) → next tick's due-jobs query naturally includes it, no different from any other due row → claimed and dispatched normally`. Notice what's absent from this path: there is no separate catch-up mechanism, no backlog table, no special-cased "recover missed jobs" job. An overdue job is just a more extreme case of "due," and the ordinary claim-and-dispatch loop handles it without knowing anything unusual happened.

## Building blocks

**Job store** — holds job definitions, each with an indexed `next_run_time` (cross-ref [Database Indexing](../../database-design/database-indexing.md) — this index is this entire system's most important one: "find jobs due now" is the query the whole design exists to answer quickly).

**Scheduler tier, leader-elected per shard** — the job store is partitioned into shards (cross-ref [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md)), and exactly ONE scheduler instance is the active leader for each shard at a time (cross-ref [Replication & Consensus](../../hld-building-blocks/replication-consensus.md) for the leader-election mechanism, and [Distributed Locks](../../scalability-resilience/distributed-locks.md) for the lease that backs it — a time-bounded lease a scheduler instance must keep renewing to remain "the" leader for its shard). Only the current leader for a shard scans that shard for due jobs — this is what prevents two scheduler instances from both noticing the same due job and triggering it twice.

**Trigger queue and worker pool** — a claimed, due job is published as a trigger event to a queue (cross-ref [Message Queues & Pub/Sub](../../hld-building-blocks/message-queues-pubsub.md)), and a separate, horizontally-scaled worker pool consumes trigger events and actually executes the job payload, independently of the scheduler's own capacity.

**Run history store** — every trigger's outcome (success, failure, retry count) is recorded separately from the job definition itself, matching the reasoning this guide's [payments case study](../payments-system/00-overview.md) uses for keeping a payment's lifecycle separate from the order's: a job definition's identity and a specific run's outcome are different things with different retention needs.

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Per-shard ownership | Leader election (long-lived lease) | A distributed lock, re-acquired every tick | Continuous scanning is a long-lived responsibility; re-acquiring a lock on every tick is far more expensive than a lease a leader holds and periodically renews |
| Claiming due jobs | Bounded batch per tick (`limit=BATCH_SIZE`) | Claim every due job in one pass, unbounded | An unbounded claim during a round-number clustering burst could pull 50,000 jobs into one tick and overwhelm the trigger queue and worker pool instantly; bounded batches smooth the burst across several ticks |
| Claim-to-trigger durability | Transactional outbox (claim + outbox write, same transaction; relay publishes) | Publish to the trigger queue directly, right after the claim | A crash between claim and a direct publish would silently lose the trigger — the job shows `claimed` but nothing ever runs it; the outbox makes the publish durable regardless of when the relay gets to it |
| "Is this job due" check | The database's own clock (`next_run_time <= NOW()` evaluated inside the query) | Each scheduler instance's local system clock | Sidesteps clock skew between scheduler processes entirely — a scheduler with a fast local clock can't fire early or disagree with a slow-clocked peer about what's due |
| Missed-schedule handling | No special catch-up logic — overdue is just still-due | A dedicated backlog/catch-up queue for jobs missed during downtime | Introducing a separate path would duplicate the exact same claim-and-dispatch logic for no benefit — "due" already covers "overdue" |

## Load Handling

- **Peak-vs-average tolerance:** the average from Capacity Estimation (~1,400 triggers/sec) is not the number that stresses this design — the real risk is round-number clustering: many jobs scheduled for "the top of the hour" all becoming due within the same second. This is a predictable, *recurring* pattern (it happens every hour), not a rare black-swan spike, which is exactly why the claim step is bounded by default rather than as an emergency measure.
- **Where backpressure kicks in first:** at the claim itself. Bounded-batch claiming caps how many due jobs one tick dispatches, regardless of how many are actually due at that instant — the remainder simply waits for the next tick, a few seconds later, comfortably inside this system's own "trigger accuracy within a few seconds" requirement.
- **What gets shed under overload:** nothing is ever silently dropped. A due job not claimed this tick is still sitting in `status='due'` and gets picked up next tick — "shedding" here just means deferring dispatch by one tick interval, never losing the trigger the way dropping a request would in a typical read path.
- **Autoscaling lag:** the worker pool (which actually executes job payloads) autoscales on its own 1–3 minute horizon, same as any stateless tier. The trigger queue is what absorbs the gap between a burst being claimed and the worker pool catching up to execute all of it — the scheduler's own claim-and-dispatch throughput isn't gated by how fast workers can currently execute.
- **Load-test target:** sustain a burst of 50,000 simultaneously-due jobs (all with `next_run_time` landing in the same second) and confirm every one is claimed and dispatched within 10 seconds, with zero duplicate claims and zero jobs left permanently un-triggered.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| Split-brain during failover — two schedulers both believe they're leader for the same shard | Atomic conditional claim: `UPDATE jobs SET status='claimed', claimed_by=? WHERE id=? AND status='due'` — the row's own current state is part of the `WHERE` clause, so only one claimant's update can match | The losing scheduler's update affects zero rows; its code simply moves to the next due job in its batch, no error, no retry logic needed |
| A new leader resumes scanning a shard whose due jobs were already claimed moments earlier by the outgoing leader | Same atomic claim — by the time the new leader's due-jobs query runs, those rows are already `claimed` (or already reset to `due` for their *next* occurrence), so they don't reappear in its result set, or its own claim attempt fails the identical `WHERE` clause | The new leader silently sees a smaller due-jobs set than it might have expected; no coordination call to the outgoing leader is needed, and no job is double-triggered |
| The same scheduler process's own tick overlapping itself — a slow tick still running when the next tick's timer fires | An in-process reentrancy guard skips starting a new tick if the previous one hasn't finished. This is the one race actually worth an application-level guard, unlike the two above — it's the only race that's *within* one process, so the database's cross-process claim can't be what closes it | The overlapping tick attempt simply doesn't start; nothing claims twice from a single process racing its own clock |

## Scaling & Reliability

- **Horizontal scaling:** adding more shards adds more parallel leaders, each independently scanning a smaller slice of the job store — scheduling throughput scales with shard count, the same reasoning [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) applies generally.
- **Circuit breaker:** the outbox relay's publish to the trigger queue is wrapped in a circuit breaker (cross-ref [Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md)) — if the queue is unreachable, the relay backs off rather than hammering it; claimed jobs' outbox rows simply wait, durably, rather than being lost.
- **Retries:** the relay retries a failed publish against the *same* outbox row — never re-claiming the job, never regenerating the idempotency key — bounded with backoff, since the row itself durably persists the intent to publish no matter how many attempts it takes.
- **Dead-letter queue:** an outbox row that fails to publish after N attempts (a malformed payload, a permanently broken downstream consumer) moves to a dead-letter table rather than blocking the relay from processing every *other* shard's rows queued behind it.
- **Graceful degradation:** if the trigger queue is fully down, claimed jobs simply accumulate as durable, unpublished outbox rows — once the queue recovers, the relay drains the backlog directly; nothing needs to be re-claimed or recomputed, because the claim and the intent-to-publish were already committed atomically.
- **Multi-region:** not built here — named as a real gap below rather than glossed over.

## What you'd revisit as this grows

- **Dynamic shard rebalancing.** This design assumes a roughly even spread of jobs across shards; real usage skews, and a mature system needs to detect a "hot" shard accumulating disproportionately many jobs and rebalance it, not just add shards uniformly.
- **Priority within a shard.** A bounded batch claims *some* due jobs each tick, but this design doesn't distinguish a time-sensitive trigger from a low-priority one when a burst forces a choice about ordering — worth naming as a gap rather than assuming it doesn't matter.
- **Cross-job dependencies** (job B must run only after job A's most recent run succeeded) are explicitly out of scope for this module's single-job-at-a-time claim-and-dispatch model, and would need a genuinely different mechanism layered on top.
- **Multi-region active-active scheduling**, with the same conflict-avoidance care this guide's payments case study names for its own multi-region ledger gap — a much harder problem than this design takes on.
