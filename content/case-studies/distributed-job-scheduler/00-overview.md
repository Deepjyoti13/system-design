# Module 00 — Overview

## Requirements

**Functional:**
- Schedule one-off jobs (`run_at`) and recurring jobs (a cron-style expression).
- Trigger each scheduled job **exactly once** per scheduled time — not zero times (a missed trigger) and not more than once (a duplicate trigger), even with multiple scheduler instances running for availability.
- Execute the triggered job reliably, retrying on failure, and record the run's outcome.

**Non-functional** (stated as assumptions, interview-style):
- Millions of scheduled jobs across all tenants.
- Trigger accuracy within a few seconds of the scheduled time — this isn't a hard-realtime system, but "an hour late" is a bug, not a rounding error.
- The scheduler itself must be highly available; it cannot be a single process that takes every scheduled job down with it if it crashes.

## Capacity Estimation

Using this guide's [back-of-envelope method](../../foundations/back-of-envelope-estimation.md):

- **Job volume:** 5M active job definitions, with an average trigger interval of roughly once per hour: 5M / 3,600 ≈ **~1,400 triggers/sec** average.
- **Peak clustering:** many recurring jobs are scheduled on round boundaries (every job set to "top of the hour" all firing in the same second) — the real peak can be a large multiple of the average within a single second, which is why the design below claims and dispatches due jobs in small batches rather than assuming triggers are ever smoothly spread out.
- **Job-run history:** if each trigger produces one run record retained for 90 days: 1,400/sec x 86,400 x 90 ≈ **~10.9B run records** — large enough that run history needs its own scaling story, separate from the much smaller table of active job definitions.

## Approach Walkthrough

Each job definition stores its own `next_run_time`. A scheduler component continuously looks for job definitions whose `next_run_time` has passed, claims them, and hands each one off to a worker pool for actual execution — then computes and stores the job's NEXT `next_run_time` (for recurring jobs) so the same job is found and triggered again at its next scheduled time. The scheduler's job is purely "notice it's due and hand it off exactly once"; the worker pool's job is "actually run it, with retries."

## API Surface

- `POST /jobs {cron_expr | run_at, payload, retry_policy}` -> `{job_id}`
- `DELETE /jobs/{job_id}` — cancel a scheduled job.
- `GET /jobs/{job_id}/runs` -> `[{run_id, status, started_at, finished_at}]`
