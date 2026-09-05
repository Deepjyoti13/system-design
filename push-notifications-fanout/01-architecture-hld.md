# Module 01 — High-Level Design (HLD)

**Diagram for this module:** [Push Notifications — architecture diagram](https://claude.ai/code/artifact/2a45f97d-7ed6-4575-90ad-4edf20c812aa)

## Requirements, before any boxes

**Functional requirements:**
- Given an event (post created, 2FA code generated, marketing campaign triggered) and a set of recipients, deliver a push notification to every valid registered device for those recipients.
- Support two provider networks: Apple Push Notification service (APNs) for iOS, Firebase Cloud Messaging (FCM) for Android and web.
- A user can have multiple devices; one logical event should reach each of their devices exactly once, not once-per-retry.
- Distinguish **transactional** (2FA, security alerts) from **bulk** (social fan-out, marketing) traffic, because they have opposite latency needs.
- Prune device tokens the provider reports as dead, so the system stops paying to send into the void.

**Non-functional requirements (assumed, stated explicitly):**
- **Scale:** ~5B notifications/day on average. A single "celebrity post" event can fan out to 50M devices within a 60-second window — that burst, not the daily average, is the number that shapes this architecture.
- **Latency:** transactional notifications must reach the provider handoff in under 2 seconds at P99. Bulk notifications have no per-message SLA, but a 50M-device fan-out should fully complete within ~5 minutes.
- **Availability:** APNs being slow or down must not affect FCM delivery, and vice versa — the two are independent failure domains.
- **Delivery guarantee:** at-least-once at the infrastructure level is acceptable (an occasional duplicate push is a minor annoyance, not a correctness bug); an *unbounded* flood of duplicates to one device is not acceptable.

The two facts that drive everything below: (1) burst-to-average ratio is roughly **14x** (833K/sec peak vs. 58K/sec average — see math below), and (2) transactional and bulk traffic must never share a resource that lets one starve the other.

## The building blocks

- **Ingest API** — stateless HTTP endpoint producer services call with an event + recipient info. Does almost no work itself: validates, assigns an `event_id`, writes to a queue. Kept deliberately thin so it's never the bottleneck.
- **Target resolver (worker)** — for a 1:1 event (2FA), the producer already supplies the one device; for a broadcast event (a post), this step expands "user X" into "N million follower device tokens," reading from the Device Token Store in batches.
- **Kafka** — two logically separate topic groups: `notifications.transactional` and `notifications.bulk`. Separate topics, separate consumer groups, separate worker pools — this is *the* mechanism that stops bulk traffic from delaying transactional traffic, not a priority field on a shared queue.
- **Dispatch workers** — consume per-device jobs, check the dedup cache, call the right provider adapter, record the result.
- **Provider adapters** — `ApnsProvider` / `FcmProvider` behind one interface (module 02 covers this).
- **Device Token Store** — holds `user_id → [device tokens]`, keyed for point lookups only.
- **Dedup cache (Redis)** — short-lived claim per `(event_id, device_token)` so a redelivered job doesn't double-send.
- **Token invalidation pipeline** — providers tell you a token is dead (HTTP 410 / `NotRegistered`); this async path prunes it from the Device Token Store instead of the hot path doing it inline.

## Monolith vs. microservices — an explicit call

This is **not** one monolith, but it's also not a dozen nanoservices. Three deployable units:

1. **Ingest API** — owned by the platform team, called by every producer team (posts, security, marketing). A stable, versioned API boundary matters here because those teams don't want to redeploy when internals change.
2. **Dispatch workers**, split into two independently-scaled pools (transactional / bulk) — this split is the actual reason this isn't a monolith: the two traffic classes need to scale on completely different triggers (transactional scales on request rate; bulk scales on queue depth), and a shared process would mean a code change or incident in one path risks the other.
3. **Token Registry** — owns the device token store and the invalidation pipeline; separated because its data model (device lifecycle) changes on a different cadence than dispatch logic, and because target resolution (a read-heavy path) and registration (a write path from every app install) have very different traffic shapes.

What this deliberately does **not** split further: provider adapters live inside the dispatch worker pool, not as their own service — there's no independent scaling or team-ownership reason to pull them out, and doing so would just add a network hop for no benefit.

## The worked design

Open the [architecture diagram](https://claude.ai/code/artifact/2a45f97d-7ed6-4575-90ad-4edf20c812aa) alongside this.

**Registration path — a device registers or rotates its token**
`App → Ingest API → Token Registry → Device Token Store (UPSERT on (device_id, platform))`
Low volume, correctness-critical (see the Concurrent-user handling section below for why UPSERT, not delete-then-insert).

**Transactional path — a 2FA code**
`Producer → Ingest API → notifications.transactional (Kafka) → Transactional Dispatch Worker → dedup claim (Redis) → ApnsProvider/FcmProvider (single call, not batched) → Client device`
This path has its own reserved worker pool and its own queue, so it is never queued behind bulk traffic regardless of how large a bulk fan-out is in flight.

**Bulk/social path — a celebrity posts**
`Producer → Ingest API → notifications.bulk (Kafka) → Target Resolver (expands to N device jobs, partitioned by hash(device_token)) → notifications.dispatch.bulk → Bulk Dispatch Workers (batched provider calls) → dedup claim → Providers`
Partitioning dispatch jobs by `hash(device_token)` — not by user or by event — is what spreads a single celebrity's 50M-device fan-out evenly across every worker instead of piling onto one.

**Async path — token invalidation**
`Dispatch Worker (on 410/NotRegistered) → notifications.token-invalid → Token Registry pruner → Device Token Store (is_valid = false)`
Decoupled from the send path so a batch of dead tokens never slows down a live send.

## Back-of-envelope math

- 5B notifications/day ÷ 86,400s ≈ **58,000/sec average**.
- 50M devices in 60s ≈ **833,000/sec peak** for a single mega-broadcast — roughly **14x** average. This is the number the Load Handling section below is designed against.
- Device Token Store: assume 2B devices, ~200 bytes/row → ~400GB. Comfortably shardable, not a storage problem.
- Delivery/audit log: 5B/day × 150 bytes × 90-day retention ≈ **67.5TB** — large enough that it does *not* belong in the same store as the hot dedup path (see DB design).

## Trade-offs to make explicit

| Decision | Choice made | Alternative | Why |
|---|---|---|---|
| Queue vs. direct provider call | Queue (Kafka) between ingest and dispatch | Ingest API calls APNs/FCM synchronously | A synchronous call means a producer's burst directly hits APNs/FCM rate limits and the producer blocks or fails when a provider is slow. The queue absorbs the burst and lets dispatch workers pace calls within each provider's own rate limit. |
| SQL vs. NoSQL for Device Token Store | Key-value / wide-column NoSQL, partitioned by `user_id` | Relational `device_tokens` table with FK to `users` | The only query pattern is a point lookup by `user_id`; there's no join need, and a 2B-row table needs horizontal scale a single relational primary doesn't give for free. |
| Redis dedup cache vs. DB unique constraint | TTL'd Redis claim, 24h window | Unique constraint on `(event_id, device_token)` in a durable store | The check runs at up to 833K/sec and only needs to hold state for the retry window — a permanently-growing unique index would need pruning anyway, at a much higher write cost than a cache. |
| Batched vs. per-notification provider calls | Batched for bulk, per-notification for transactional | One rule for both | Batching reduces per-call overhead and respects provider rate limits efficiently, but adds a batch-fill wait that a 2-second transactional SLA can't afford — so the two traffic classes get opposite answers, deliberately. |
| Own fan-out vs. FCM Topics | Own target resolution + dispatch | Let FCM's built-in Topics feature handle broadcast fan-out | FCM Topics only covers Android/Web, not APNs — it can't produce one consistent cross-platform pipeline, and it removes control over priority separation, retry visibility, and dedup. |

## Load handling

- **Peak-vs-average tolerance:** this design absorbs ~14x average load (58K/sec avg → 833K/sec peak) without shedding anything, because ingest is decoupled from dispatch by the queue — ingest's job is O(1) enqueue work, so ingest throughput is never the bottleneck.
- **Where backpressure kicks in, in order:** (1) queue depth grows first — this is *intended*, Kafka is built to retain a large backlog; (2) if dispatch lag (time from enqueue to actual send) crosses a threshold (2 minutes for bulk), autoscaling adds dispatch worker instances; (3) if a provider itself rate-limits (429s from APNs/FCM), workers back off exponentially *per provider* and the backlog grows further in the queue rather than anything being dropped.
- **What gets shed:** nothing, by design — push semantics tolerate lateness, so this system chooses "eventually delivered" over "dropped." What's throttled is the *rate* of provider calls, not whether a notification is attempted at all. Transactional traffic is immune to bulk backlog because it has its own topic and worker pool, not because it has higher "priority" on a shared one.
- **Autoscaling lag:** new dispatch worker instances take roughly 2–3 minutes to start and warm their provider connection pools. The queue is exactly what absorbs load during that lag — this is the real justification for a durable queue over an in-memory buffer, which would lose everything on a worker restart.
- **Load-test target:** sustain 900K dispatch jobs/sec across the bulk worker fleet with dispatch-lag P99 under 5 minutes, and separately sustain the transactional path at its normal rate with P99 end-to-end latency under 2 seconds, both for 10 minutes continuous.

## Concurrent-user handling

- **Race 1 — the same job is picked up twice.** A Kafka consumer-group rebalance (or a worker crashing after processing but before committing its offset) can redeliver a job to a second worker. **Mechanism:** before calling the provider, a worker executes `SETNX sent:{event_id}:{device_token}` in Redis with a 24h TTL. Only the worker that wins the `SETNX` proceeds to send. **What the loser sees:** nothing — it silently no-ops. This isn't an error state; the notification was genuinely handled by the winner.
- **Race 2 — a token goes invalid while another in-flight job is about to use it.** Two different events for the same device could be mid-flight when a provider reports the token dead. **Mechanism:** token invalidation is a soft-delete flag (`is_valid = false`) applied with a plain `UPDATE`, not a lock. **What happens:** the other in-flight job may waste one send attempt against the now-dead token before it, too, gets a 410 and marks it invalid — this is an explicit, accepted cost (a bounded number of wasted provider calls), not an oversight; there's no correctness issue to protect against here, so no lock is worth adding.
- **Race 3 — token rotation on app reinstall.** A device re-registering can momentarily race a stale "de-register" call from the same physical device restarting. **Mechanism:** registration is an `UPSERT` (`INSERT ... ON CONFLICT (device_id, platform) DO UPDATE`), never a delete-then-insert — so there is never a window where the device has zero valid tokens, and the unique constraint on `(device_id, platform)` guarantees exactly one current token per device.

## What you'd revisit as this grows

- The bulk dispatch worker pool is the first thing to hit a ceiling — not on CPU, but on how fast target resolution can enumerate a 50M-follower list; that step would itself need to be paginated/sharded well before dispatch does.
- A single Redis cluster backing the dedup cache becomes a shared dependency across every dispatch worker; at higher scale it's worth partitioning it (e.g., by device-token hash) rather than one cluster absorbing all 833K/sec of claims.
- This design assumes one region. Multi-region would mean either routing a user's notifications to their nearest region's dispatch pool (and replicating the Device Token Store) or accepting cross-region calls to providers — worth knowing this exists, not worth building for a first version.
