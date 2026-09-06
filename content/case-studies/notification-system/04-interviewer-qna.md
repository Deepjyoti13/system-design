# Module 04 — Interviewer Q&A

**1. What happens when two requests hit the same resource at the same instant — specifically, a triggering service's retry publishing the same event twice concurrently?**
The unique constraint on `notifications(source_service, idempotency_key)` means only one insert can win; the second concurrent request's insert fails the constraint and its handler returns the *first* request's already-assigned `notification_id`, with no second fan-out ever dispatched.

**2. What happens when traffic spikes 10x for an hour — a platform-wide announcement, say?**
The Ingestion API and channel workers scale horizontally since both are stateless per job; the harder constraint is each provider's own rate limit, which this system can't scale around by adding more workers — queuing with backoff against that external ceiling absorbs the burst, and per Load Handling, only low-priority categories widen their digest window, never the transactional ones.

**3. Why re-check the user's preference at the channel worker, when it was already resolved once at ingestion?**
Because a user can flip a toggle in the gap between ingestion and a worker actually picking up that channel's job — trusting the ingestion-time snapshot would mean occasionally sending on a channel the user had *already* turned off by the time it actually sent, which is exactly the failure mode a preference system exists to prevent.

**4. Why is this its own service instead of a shared library every team imports?**
A shared library still means every team's deploy has to pick up the latest version to get a dedup or preference-logic fix — a live service means the fix ships once and every caller gets it immediately, and it isolates a channel provider's flakiness from the services that merely trigger notifications, which a library can't do.

**5. How would you batch several related notifications into one digest instead of spamming a user?**
Low-priority categories delay by a short window at fan-out time, collecting events for the same user into one summary send instead of one-per-event; transactional categories (security, 2FA) skip batching entirely, the same distinction Module 01's Load Handling draws for what's allowed to shed under pressure.

**6. What's the failure mode if the SMS provider is down for an hour?**
The SMS channel's circuit breaker trips and its jobs queue for retry with backoff, landing in the SMS-specific dead-letter queue if they exhaust retries — push, email, and in-app deliveries for the same or any other notification are completely unaffected, since each channel owns its own queue.

**7. Why not just have every triggering service call the push/email/SMS providers directly?**
Because then preference enforcement, dedup, and per-provider retry logic all have to be correctly reimplemented in every one of those services — get it wrong in just one and a real user gets spammed, or a compliance-relevant opt-out is silently ignored, with no single place to find or fix it.

**8. How would you test the channel-sending logic without hitting a real provider on every test run?**
A mock implementation of the `ChannelSender` interface per channel, exercised with contract tests asserting its response shape matches the real provider's documented API — `ChannelWorker` itself never needs to know whether it's talking to a mock or the real thing, the same interface-over-implementation pattern this guide applies to every pluggable dependency.

**9. Would you shard `notifications` and `deliveries` by a hash of the notification ID for even distribution, the way some high-write systems in this guide do?**
No — a hash spread would scatter one user's notification history across every shard, making "show me my notifications" a fan-out-and-merge query instead of a single-shard lookup. `user_id` is the key that matches the query that actually runs constantly, the same reasoning [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) gives for choosing a shard key from the access pattern, not from even-distribution alone.

**10. What happens to an in-flight fan-out job if the channel-worker instance handling it is killed mid-send?**
The job's `status` never advanced past `claimed` on that instance, so a health-check/reaper sweep (the same "stuck job" pattern this guide's [Distributed Job Scheduler](../distributed-job-scheduler/02-lld.md) uses) eventually resets it back to `queued` after a timeout, and a healthy worker instance claims and sends it — the atomic claim means this can happen without any risk of two workers believing they both own it at once.
