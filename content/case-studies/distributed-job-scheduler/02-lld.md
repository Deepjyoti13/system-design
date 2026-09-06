# Module 02 — Low-Level Design

![Split-brain: two schedulers both believe they hold the lease, but only one's conditional claim update actually affects a row](diagrams/lld.svg)

## Interfaces vs. implementations

- **`JobStore`** *(interface)* → **`SqlJobStore`** — `queryDue(shard, limit)` (the bounded-batch scan), `claim(jobId)` (the atomic conditional update), `updateNextRun(jobId, nextRunTime)` for recurring jobs.
- **`LeaseManager`** *(interface)* → **`EtcdLeaseManager`** / a database-row-based implementation — `haveValidLease(shardId)`, `renew(shardId)`, backing the leader-election mechanism from Architecture & HLD.
- **`TriggerPublisher`** *(interface)* → **`OutboxTriggerPublisher`** — `publish(jobId, payload, idempotencyKey)`, writing to the outbox in the same transaction as the claim.
- **`Scheduler`** — the orchestrator running the `tick()` loop. Depends on all three interfaces, implements none of the storage or coordination itself.

## Claim-and-dispatch loop

Pseudocode (run only by the current shard leader):
```
Scheduler.tick():
    if not leaseManager.haveValidLease(shard_id):
        return   # not the leader right now; do nothing

    due_jobs = jobStore.queryDue(shard_id, limit=BATCH_SIZE)   # bounded batch, not unbounded

    for job in due_jobs:
        claimed = jobStore.claim(job.id)   # UPDATE ... WHERE status='due'
        if not claimed:
            continue   # another process already claimed it; not our race to win

        triggerPublisher.publish(job.id, job.payload,
                                  idempotencyKey=f"{job.id}:{job.next_run_time}")
        job.next_run_time = computeNext(job.cron_expr)   # recurring jobs only
        jobStore.updateNextRun(job.id, status="due", next_run_time=job.next_run_time)
```
The `idempotency_key` combining `job_id` and the SPECIFIC scheduled time it fired for (not just `job_id` alone) is what lets a worker safely retry a trigger without worrying it's silently re-triggering a DIFFERENT scheduled occurrence of the same recurring job (cross-ref [Idempotency Keys](../../scalability-resilience/idempotency-keys.md)).

**Missed-schedule catch-up**: if the scheduler itself was down when a job was due (not a worker failure — the scheduler process itself unavailable), the job's `next_run_time` is simply still in the past once a leader resumes scanning, and it's picked up and triggered on the next tick, no different from any other due job — the design doesn't need special catch-up logic because "overdue" is just a more extreme case of "due."

## Concurrency at the code level

`jobStore.claim(jobId)` needs no in-process lock, and this is worth stating explicitly: many scheduler instances run concurrently, so a language-level mutex would only ever protect against other threads *on the same instance* — it would do nothing about a peer instance, or a split-brain second leader, claiming the same row a moment later. Correctness comes entirely from the conditional `UPDATE` being enforced by the database itself, the same discipline this guide applies everywhere two writers might race for the same row: push the atomicity requirement down into the one system that can actually provide it for free.

The one place an in-process guard *is* worth adding is the reentrancy case from Architecture & HLD's Concurrent-User Handling table: the same process's own tick overlapping itself. That's a genuinely different kind of race — it's within one process, not across processes — so a lightweight flag ("is a tick already running?") is the right tool, not a distributed coordination primitive. Reaching for a distributed lock to solve a single-process problem would be solving the wrong layer's race.

## Design patterns you just used, named

- **Repository pattern** — `JobStore` hides storage behind method calls; the `Scheduler` never issues SQL directly.
- **Strategy pattern** — `LeaseManager`'s specific backing (etcd, Zookeeper, or a database-row-based lease) is swappable behind one interface without the `Scheduler` knowing which.
- **Transactional outbox** — `TriggerPublisher`'s outbox-backed implementation is this pattern by name, the same one this guide's payments case study uses for its webhook relay: durably queue an event in the same transaction as the state change it describes.
- **Leader election / lease pattern** — worth naming explicitly, since it's the load-bearing coordination primitive this entire design depends on: one long-lived, renewable ownership claim per shard, rather than a lock re-acquired on every operation.

## Practice: extend it yourself

Before moving to Database Design, sketch (pseudocode is fine) how you'd add:

1. **Job priority within a shard** — some due jobs are more time-sensitive (a payment retry) than others (a nightly report). A bounded batch can't claim everything at once during a burst — which part of the claim-and-dispatch loop changes to claim high-priority jobs first, and does the `next_run_time` index still serve that ordering, or does it need a second column?
2. **Dependent jobs** — job B must only run after job A's most recent run succeeded. Does this dependency get checked inside `claim()` itself, or as a precondition the worker pool enforces right before executing B's payload? What should happen if A's run is still in progress when B becomes due — does B wait, skip, or fail loudly?

Neither has one clean answer — the point is noticing which interface (`JobStore`, or the worker pool's own execution logic) is the natural home for each piece of new behavior, before you've fully worked out what that behavior should do.
