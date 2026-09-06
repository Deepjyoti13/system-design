# Design an Ad Click Aggregation Pipeline

![Clicks streaming through tumbling windows with a watermark grace period, deduplicated before they ever reach the aggregate counter](diagrams/hld.svg)

## Requirements

**Functional:**
- Ingest ad-click events fired from wherever an ad is shown (a redirect, a tracking pixel).
- Aggregate click counts per `(ad_id, time_window)` for advertiser billing and reporting.
- Never double-count the same click, even if the client retries the tracking request.
- Tolerate events arriving a little late (network delay between the click and the tracking beacon reaching the ingestion tier) without producing a wrong final count.

**Non-functional** (stated as assumptions, interview-style):
- 1M clicks/sec at peak (a major campaign launch).
- Aggregated counts need to be available within a few minutes of a time window closing — advertisers check dashboards, they don't need sub-second billing numbers.
- An advertiser must never be billed for a duplicate or fraudulent click — correctness of the count matters more than how fast it appears.

## Capacity Estimation

Using this guide's [back-of-envelope method](../../foundations/back-of-envelope-estimation.md):

- **Ingestion rate:** 1M clicks/sec peak. At roughly 200 bytes/event (ad_id, timestamp, user/session id, metadata): **~200MB/sec** raw ingestion bandwidth at peak.
- **Raw event storage:** if raw click events are retained for 30 days for audits/fraud review: 200MB/sec × 86,400 × 30 ≈ **~500PB** — large enough that raw events clearly need cold, cheap storage, not a hot database (cross-ref [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md)).
- **Aggregate storage:** the AGGREGATED counts are tiny by comparison — one row per `(ad_id, minute)` instead of one row per click. With, say, 10M active ads and 1,440 minutes/day: ~14.4B aggregate rows/day, each a few dozen bytes — orders of magnitude smaller than the raw stream, which is the entire point of aggregating.

## Approach Walkthrough

A click event is fired, lands in an ingestion queue, and is processed by a stream aggregation layer that buckets it into a fixed time window (e.g. one-minute tumbling windows) and increments that window's running count. A window isn't finalized and written to the durable aggregate store the instant its time boundary passes — it waits a short grace period for straggling, slightly-late events first. A serving API then reads from the durable aggregate store, not from the live stream, so an advertiser's dashboard query never touches the hot ingestion path.

## API Surface

- Click ingestion (fire-and-forget from the ad-serving side): `POST /clicks {ad_id, click_id, client_timestamp, session_id}`.
- `GET /campaigns/{ad_id}/stats?window_start=&window_end=` -> `{ windows: [{window_start, click_count, unique_click_count}] }`.

## High-Level Design

**Ingestion** — every click lands in a partitioned log ([Kafka & the Distributed Log](../../hld-building-blocks/kafka-distributed-log.md)), partitioned by `ad_id` so that all of one ad's events land in the same partition and are processed in the order they arrive there.

**Deduplication, before a click is ever counted** — each click carries a client-generated `click_id`; the stream processor checks it against a short-lived dedup cache (cross-ref [Idempotency Keys](../../scalability-resilience/idempotency-keys.md) — the exact same "have I already processed this exact request" mechanism, applied to a click event instead of a payment) before incrementing anything. A retried tracking beacon for the same click is a cache hit, not a second increment.

**Windowed stream aggregation** — the stream processor groups events into **tumbling windows** (fixed, non-overlapping time buckets, e.g. one minute each) and maintains a running count per `(ad_id, window)` in memory as events arrive.

**Watermarks, precisely, as the answer to "when is a window actually done"** — a window's time boundary passing doesn't mean every one of its events has arrived yet; a watermark is an explicit, moving threshold ("we believe all events up to time T have now arrived") that the pipeline advances based on observed event timestamps and a configured allowed lateness (e.g. 2 minutes). A window is only finalized and flushed to the durable aggregate store once the watermark passes its end boundary — an event that arrives after that grace period is either dropped or routed to a small "late data" correction path, a decision made explicitly rather than silently.

**Aggregate store** — the finalized, durable per-`(ad_id, window)` counts (cross-ref [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) for sharding this by `ad_id`, matching the query pattern "give me this ad's counts over time").

**Load Handling.** The defining load characteristic here is that ingestion volume and serving-query volume are almost completely decoupled — a 10x spike in click volume (a viral ad) never touches the serving path at all, since dashboards read finalized aggregates, not the live stream. The stream processing tier itself scales horizontally by adding more partition consumers (cross-ref [Kafka & the Distributed Log](../../hld-building-blocks/kafka-distributed-log.md)'s partition/consumer-group model), and a sustained ingestion spike that outpaces processing capacity backs up in the queue rather than being dropped — cross-ref [Backpressure, Load Shedding & Bulkheads](../../scalability-resilience/backpressure-load-shedding.md): a growing queue is the correct, visible signal to autoscale on, not silent data loss.

**Concurrent-User Handling.** The core race is two (or more) events for the SAME click arriving to different stream-processor instances at nearly the same time (a genuine retry, or an ad-serving bug double-firing the beacon) — resolved by the dedup cache lookup being a single atomic check-and-set (the identical mechanism [Idempotency Keys](../../scalability-resilience/idempotency-keys.md) uses: a unique constraint on `click_id` closes the race, not a read-then-write check), so only the first arrival increments the count and every subsequent one is recognized as already-seen. A second race — two partition consumers both trying to finalize and flush the SAME window at once as the watermark passes — is resolved by having exactly one consumer own each partition at a time (cross-ref [Kafka & the Distributed Log](../../hld-building-blocks/kafka-distributed-log.md)'s consumer-group assignment), so window finalization for a given ad's events is never contended in the first place.

## Low-Level Design

**Window assignment and watermark-triggered finalization**, pseudocode:
```
StreamProcessor.onEvent(click):
    if DedupCache.seen(click.click_id):
        return   # already counted, ignore
    DedupCache.markSeen(click.click_id)   # atomic check-and-set

    window = tumblingWindowFor(click.client_timestamp, size=1_minute)
    RunningCounts[click.ad_id][window].increment()

    watermark = max(watermark, click.client_timestamp - allowedLateness)
    for w in windowsEndingBefore(watermark):
        if not w.finalized:
            AggregateStore.write(w.ad_id, w, RunningCounts[w.ad_id][w])
            w.finalized = True
```
A late event arriving for an already-finalized window is routed to a separate correction record rather than silently mutating a count an advertiser may have already been billed against — the pipeline makes the correction visible instead of quietly changing history.

## Database Design & Scaling

- **Aggregate store:** `(ad_id, window_start) -> {click_count, unique_click_count, finalized_at}`, sharded by `ad_id` — every serving query is "this ad's counts over some time range," so keeping one ad's windows together avoids fan-out on the dominant read.
- **Dedup cache:** a short-lived, TTL-based key-value store (cross-ref [Caching Strategies](../../hld-building-blocks/caching-strategies.md)) keyed by `click_id` — it only needs to retain entries slightly longer than the allowed-lateness window, not forever, since a click older than that has already either been counted or dropped for good.
- **Raw click log:** append-only, cold storage (cross-ref [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md)), kept for audit/fraud-review purposes — never read on the serving path, only by offline analysis.

## Interviewer Q&A

**What happens when two requests hit the same resource at the same instant?**
Two arrivals of the same `click_id` — a genuine retry or a double-fire bug — race on the dedup cache's check-and-set; because that's a single atomic operation (the same unique-constraint mechanism [Idempotency Keys](../../scalability-resilience/idempotency-keys.md) uses), exactly one arrival wins and increments the count, and every other arrival for that `click_id` is recognized as a duplicate and dropped, never double-counted.

**What happens when traffic spikes 10x for an hour?**
Ingestion backs up in the partitioned queue rather than dropping events, and more partition consumers are added to drain the backlog faster — this only delays how quickly windows finalize, it doesn't change what gets counted, since aggregation correctness depends on the watermark eventually catching up, not on processing happening in real time.

**How would you handle an event that arrives after its window has already been finalized and billed?**
Route it to a separate, explicit correction record rather than mutating the already-finalized count — an advertiser's bill is a historical fact once emitted, and a late-arriving click becomes a visible adjustment against it instead of a silent retroactive change nobody can audit.

**Why tumbling windows instead of sliding windows here?**
Billing needs a click to belong to exactly one window, not several overlapping ones — a sliding window (where one event contributes to multiple overlapping windows) is the right tool when you want a smoothed, continuously-updating metric, not when you need a click counted exactly once against exactly one billing period.

**How would you detect and filter fraudulent clicks (e.g. a bot clicking the same ad rapidly)?**
Layer fraud detection as a separate pass over the same ingested stream rather than folding it into the counting logic itself — a velocity check (too many clicks from the same session/IP in too short a window) or a model-based score can flag and exclude suspicious clicks from the billed count, without the core windowing/dedup mechanism needing to know why a click was excluded.

**Would you recompute historical aggregates if you found a bug in the aggregation logic after the fact?**
Yes, and this is exactly why the raw click log is retained separately in cold storage — a batch reprocessing job can replay the raw events for the affected time range through corrected logic and republish the aggregate store's rows for that range, which the serving layer treats identically to any other write.

**How would you avoid the dedup cache growing unboundedly at 1M clicks/sec?**
Give every entry a TTL only slightly longer than the allowed-lateness window (cross-ref [Caching Strategies](../../hld-building-blocks/caching-strategies.md)) — a `click_id` older than that has already been either counted or permanently dropped, so there's no correctness reason to remember it any longer, keeping the cache's size bounded by lateness tolerance rather than total click volume.

**Would a single global watermark work, or does each ad need its own?**
Watermarks are tracked per Kafka partition (and therefore, since partitioning is by `ad_id`, effectively per ad or per group of ads) rather than one global value — a burst of late events for one ad shouldn't stall window finalization for every other ad's completely unrelated, on-time stream.
