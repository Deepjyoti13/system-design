# Module 03 — Database Design & Scaling

![Schema: job_definitions with its composite (shard, status, next_run_time) index, and job_runs sharded independently by job_id](diagrams/er.svg)

- **Job definitions:** `(job_id, cron_expr/run_at, next_run_time, status, shard, retry_policy)` — indexed on `(shard, status, next_run_time)`, the composite index this system's core query actually needs (cross-ref [Database Indexing](../../database-design/database-indexing.md)'s leftmost-prefix reasoning: filter by shard first, then status, then range-scan the due timestamps).
- **Job runs:** `(run_id, job_id, triggered_at, status, retry_count)` — sharded separately, by `job_id`, since run-history queries ("this job's recent runs") are scoped per job, not per shard.
- **Scaling the scheduler itself:** more shards means more parallel leaders, each independently scanning a smaller slice of job definitions — the job store's sharding is what lets scheduling throughput scale horizontally, the same reasoning [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) applies generally.
