# Module 01 — Architecture & High-Level Design

![One ingestion point resolving preferences, then four independent channel workers, each owning one provider](diagrams/hld.svg)

## Monolith vs. microservices

Notifications are pulled into their own service for a reason that shows up the moment a second internal team wants to send one: every service that triggers a notification — orders, security, social — would otherwise need to correctly reimplement preference checks, dedup, and per-provider retry logic itself. Get that wrong in one service and a user gets spammed, or worse, a compliance-relevant opt-out gets silently ignored, with no single place to find and fix it. Centralizing the *decision* (which channels, has this already been sent) is what a shared service buys; it isn't about traffic volume.

The second, independent reason the seam holds: this system's channels have wildly different failure profiles from the services that trigger them. An SMS provider having a bad day should never slow down or destabilize the orders service that just wants to fire a "shipped" event and move on. Folding notification delivery into every triggering service would mean every one of those services inherits every channel provider's flakiness as its own. If your system only ever sends one kind of notification from one place, this split isn't buying you anything yet — say so rather than defaulting to a separate service because it looks more serious.

## Building blocks

| Block | Role |
|---|---|
| **Ingestion API** | Accepts one event per trigger; the `(source_service, idempotency_key)` uniqueness check happens here, before anything else runs |
| **Preference Service** | Per `(user_id, category, channel)` lookup of what's enabled — cache-backed, since reads vastly outnumber writes |
| **Channel Workers** (push / email / SMS / in-app) | One per channel, each owning its own provider integration, retry policy, and queue — a dead provider on one channel never touches another |
| **Delivery Tracking Store** | One row per `(notification, channel)`: queued → sent → delivered/failed, the source of truth for "did this actually go out" |

## Per-path walkthrough

**Ingestion & fan-out (write)** — `Triggering Service → Ingestion API (idempotency check, insert notification) → Preference Service (resolve enabled channels) → Channel Worker(s), one job per enabled channel → Provider (APNs/FCM/email/SMS API) → Delivery Tracking Store (status write)`. The preference lookup happens once per notification here, but each channel worker re-checks its own channel's preference again immediately before sending — see Concurrent-User Handling for why that second check matters.

**In-app read path** — `Client → LB → Notification Read Service → Delivery Tracking Store (paginated query by user_id)` — a plain read, no different in shape from any other paginated list this guide builds.

**Retry / dead-letter path (async)** — `Channel Worker (send fails) → Retry Queue (backoff) → Channel Worker (retry) → Delivery Tracking Store (status=failed) → DLQ after max attempts`. Fully decoupled per channel — an email provider stuck retrying for an hour has zero effect on push or SMS delivery for the same or any other notification.

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Where dedup/preference logic lives | Centralized in one notification service | Each triggering service calls providers directly | Reimplementing dedup and preference checks N times means N chances to get it wrong, with no single place to audit |
| Channel delivery | Independent worker + queue per channel | One worker looping over channels sequentially | A stuck email provider must never block push or in-app for the same event; independent queues isolate that |
| Dedup mechanism | Idempotency key checked at ingestion | De-duplicate at delivery time, per channel | Catching the duplicate before any fan-out work starts is strictly cheaper than fanning out four jobs and then discarding them |
| Delivery guarantee | At-least-once, with idempotent channel sends | Exactly-once end-to-end | Exactly-once through a third-party provider you don't control isn't achievable — at-least-once plus a provider-side dedup key where supported is the realistic target |
| Preference check timing | Re-checked at fan-out, per channel worker | Checked once at ingestion, reused for the whole event | A user can disable a channel in the gap between ingestion and a worker actually picking up the job — see Concurrent-User Handling |

## Load Handling

- **Peak-vs-average tolerance:** the 5x peak factor from Capacity Estimation (~5,800 events/sec, ~11,600 channel-sends/sec) is ordinary horizontal scaling for the stateless Ingestion API and channel-worker tiers.
- **Where backpressure kicks in first:** low-priority categories (a "someone liked your post") are the first thing this design gives up precision on under real pressure — not by dropping them, but by widening the digest-batching window (see Module 00's approach) so ten individual sends collapse into one. Transactional categories (security alerts, password resets) never batch and never shed, the same "never touch the critical path" discipline this guide's [Backpressure, Load Shedding & Bulkheads](../../scalability-resilience/backpressure-load-shedding.md) recommends.
- **The provider's own rate limit, not your infrastructure, is usually the real ceiling** — same pattern as this guide's [Payments System](../payments-system/01-architecture-hld.md): an SMS or email provider caps sends/sec per account regardless of how many channel-worker instances you run. Queuing with backoff against that external ceiling is what absorbs a burst, not adding more workers.
- **Load-test target:** sustain 6,000 events/sec (peak channel-send rate ~12,000/sec) for 10 minutes with delivery-tracking writes keeping pace and zero notifications silently vanishing — every one ends in sent, failed, or a DLQ entry, never nothing.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| A triggering service's retry publishes the same event twice, concurrently with the original | Unique constraint on `notifications(source_service, idempotency_key)` — the second concurrent insert fails at the database, not an application-level check that could itself race | The retry's insert fails; its handler returns the original request's already-assigned `notification_id`, no second fan-out |
| A user disables a channel in the gap between ingestion (preference resolved) and a channel worker actually picking up that channel's job | The channel worker re-reads the preference immediately before sending, rather than trusting the snapshot resolved at ingestion | The worker sees the fresh "disabled" state and skips the send silently — no error, the notification simply doesn't go out on that channel |
| Two channel-worker instances (a redeploying instance and its replacement) both pick up the same `(notification, channel)` job | An atomic claim on the job row (`UPDATE ... SET status='claimed' WHERE status='queued'`) — the same conditional-update discipline this guide's [Distributed Job Scheduler](../distributed-job-scheduler/01-architecture-hld.md) uses for its own claim | The second worker's claim affects zero rows; it moves on to the next job rather than sending a duplicate |

## Scaling & Reliability

- **Horizontal scaling:** Ingestion API and every channel worker are stateless and scale by request/queue-depth, same as any stateless tier in this guide.
- **Circuit breaker:** each channel's provider call is wrapped independently (see [Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md)) — an SMS provider timing out trips only the SMS breaker; push, email, and in-app keep working.
- **Retries:** bounded, with exponential backoff, per channel — a retry never regenerates a new provider-side idempotency key, so a send that actually succeeded but lost its response doesn't double-send.
- **Dead-letter queue:** a channel job that exhausts its retries lands in that channel's DLQ, not the shared queue — one channel's persistent failures never crowd out another channel's healthy traffic.
- **Graceful degradation:** if the Preference Service's cache is unavailable, the safe default differs by category on purpose — a transactional/security notification (password reset, 2FA code) sends anyway, since those are rarely optional and failing to deliver one is worse than an unlikely stale preference; a marketing/social notification skips and retries once the cache recovers, since silently ignoring a real opt-out is the worse failure mode there.
- **Multi-region:** not built here — see below.

## What you'd revisit as this grows

- **Multi-region provider failover.** A regional SMS gateway outage currently just retries-then-DLQs; a mature system routes to a secondary provider per region automatically, the same failover shape this guide's Payments System applies to processors.
- **Digest sophistication.** The batching window described under Load Handling is a fixed delay here; a real system tunes it per category and per user's own engagement pattern.
- **Provider delivery webhooks.** This design tracks "sent," not "opened" or "delivered-to-device" — wiring in each provider's own delivery-confirmation webhook to update the tracking store after the fact is a natural next layer, deliberately left out so the core fan-out design stays legible on its own.
- **A unified cross-channel read receipt** — right now "read" only really means anything for in-app; extending it to "the user opened the push notification" needs a per-channel read-signal, which not every provider even exposes.
