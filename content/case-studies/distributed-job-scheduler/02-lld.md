# Module 02 — Low-Level Design

![Split-brain: two schedulers both believe they hold the lease, but only one's conditional claim update actually affects a row](diagrams/lld.svg)

**Claim-and-dispatch loop**, pseudocode (run only by the current shard leader):
```
Scheduler.tick():
    if not haveValidLease(shard_id):
        return   # not the leader right now; do nothing

    due_jobs = JobStore.query(
        "next_run_time <= NOW() AND status = 'due' AND shard = ?", shard_id,
        limit=BATCH_SIZE)   # bounded batch, not unbounded

    for job in due_jobs:
        claimed = JobStore.updateIf(job.id, status='claimed', where="status='due'")
        if not claimed:
            continue   # another process already claimed it; not our race to win

        TriggerQueue.publish(job.id, job.payload, idempotency_key=f"{job.id}:{job.next_run_time}")
        job.next_run_time = computeNext(job.cron_expr)   # recurring jobs only
        JobStore.update(job.id, status='due', next_run_time=job.next_run_time)
```
The `idempotency_key` combining `job_id` and the SPECIFIC scheduled time it fired for (not just `job_id` alone) is what lets a worker safely retry a trigger without worrying it's silently re-triggering a DIFFERENT scheduled occurrence of the same recurring job (cross-ref [Idempotency Keys](../../scalability-resilience/idempotency-keys.md)).

**Missed-schedule catch-up**: if the scheduler itself was down when a job was due (not a worker failure — the scheduler process itself unavailable), the job's `next_run_time` is simply still in the past once a leader resumes scanning, and it's picked up and triggered on the next tick, no different from any other due job — the design doesn't need special catch-up logic because "overdue" is just a more extreme case of "due."
