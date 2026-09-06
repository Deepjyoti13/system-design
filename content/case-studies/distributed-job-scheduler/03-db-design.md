# Module 03 — Database Design & Scaling

![Schema: job_definitions with its composite (shard, status, next_run_time) index, and job_runs sharded independently by job_id](diagrams/er.svg)

## From entities to schema

- **Job definitions:** `(job_id, cron_expr/run_at, next_run_time, status, shard, retry_policy)` — indexed on `(shard, status, next_run_time)`, the composite index this system's core query actually needs (cross-ref [Database Indexing](../../database-design/database-indexing.md)'s leftmost-prefix reasoning: filter by shard first, then status, then range-scan the due timestamps).
- **Job runs:** `(run_id, job_id, triggered_at, status, retry_count)` — sharded separately, by `job_id`, since run-history queries ("this job's recent runs") are scoped per job, not per shard.

## Indexes

- `job_definitions(shard, status, next_run_time)` — **composite**, the one every scheduler tick actually runs: filter to this leader's shard, then to jobs still `due`, then range-scan on the timestamp. Reordering these columns would break the leftmost-prefix match and force a full scan of the shard's jobs.
- `job_definitions(job_id)` — primary key, serving direct lookups and the `DELETE /jobs/{id}` cancellation path.
- `job_runs(job_id, triggered_at)` — supports "this job's recent runs" directly, matching the sharding-by-`job_id` decision below: a job's run history always lives on one shard, never fanned out.

## Consistency

- **Job definitions:** the claim itself must be strongly consistent — this is the single non-negotiable guarantee the entire design rests on, not a tunable trade-off. A read of `next_run_time` that's even briefly stale in the wrong direction (showing a job as not-yet-due when it actually is) risks a missed trigger; showing it as due when it already was claimed risks nothing, since the atomic claim itself is the real gate.
- **Job runs:** can tolerate slightly relaxed consistency for read-heavy "show me this job's history" dashboard queries — a run record's own write already happened durably at trigger time, so a replica lag on the read side doesn't change what actually occurred, only how promptly a query reflects the very latest run.

## Scaling the schema

- **Scaling the scheduler itself:** more shards means more parallel leaders, each independently scanning a smaller slice of job definitions — the job store's sharding is what lets scheduling throughput scale horizontally, the same reasoning [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) applies generally.
- **Job runs outgrow job definitions by orders of magnitude** (Module 00's capacity math: ~10.9B run records against 5M job definitions) — exactly why it's sharded independently, by `job_id` rather than by whatever scheme `job_definitions` uses, since the two tables serve entirely different query patterns.

## Connecting it back

Look at all three modules together: Module 00's "exactly once, even with multiple scheduler instances" requirement is why Module 01 puts leader election and an atomic conditional claim at the center of the design rather than trusting coordination alone; that same requirement is why the composite index here exists — a slow due-jobs query would widen the window in which two leaders' scans could disagree about what's due; and the decision to shard `job_runs` independently of `job_definitions` is what keeps the fastest-growing table in the system from ever being the thing that makes scheduling itself slow down. Nothing in this schema is arbitrary — every column, index, and sharding choice traces back to the one requirement this case study opened with.
