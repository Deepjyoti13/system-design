# Module 04 — Interviewer Q&A

**1. What happens when two requests hit the same resource at the same instant?**
Two arrivals of the same `click_id` — a genuine retry or a double-fire bug — race on the dedup cache's check-and-set; because that's a single atomic operation (the same unique-constraint mechanism [Idempotency Keys](../../scalability-resilience/idempotency-keys.md) uses), exactly one arrival wins and increments the count, and every other arrival for that `click_id` is recognized as a duplicate and dropped, never double-counted.

**2. What happens when traffic spikes 10x for an hour?**
Ingestion backs up in the partitioned queue rather than dropping events, and more partition consumers are added to drain the backlog faster — this only delays how quickly windows finalize, it doesn't change what gets counted, since aggregation correctness depends on the watermark eventually catching up, not on processing happening in real time.

**3. How would you handle an event that arrives after its window has already been finalized and billed?**
Route it to a separate, explicit correction record rather than mutating the already-finalized count — an advertiser's bill is a historical fact once emitted, and a late-arriving click becomes a visible adjustment against it instead of a silent retroactive change nobody can audit.

**4. Why tumbling windows instead of sliding windows here?**
Billing needs a click to belong to exactly one window, not several overlapping ones — a sliding window (where one event contributes to multiple overlapping windows) is the right tool when you want a smoothed, continuously-updating metric, not when you need a click counted exactly once against exactly one billing period.

**5. How would you detect and filter fraudulent clicks (e.g. a bot clicking the same ad rapidly)?**
Layer fraud detection as a separate pass over the same ingested stream rather than folding it into the counting logic itself — a velocity check (too many clicks from the same session/IP in too short a window) or a model-based score can flag and exclude suspicious clicks from the billed count, without the core windowing/dedup mechanism needing to know why a click was excluded.

**6. Would you recompute historical aggregates if you found a bug in the aggregation logic after the fact?**
Yes, and this is exactly why the raw click log is retained separately in cold storage — a batch reprocessing job can replay the raw events for the affected time range through corrected logic and republish the aggregate store's rows for that range, which the serving layer treats identically to any other write.

**7. How would you avoid the dedup cache growing unboundedly at 1M clicks/sec?**
Give every entry a TTL only slightly longer than the allowed-lateness window (cross-ref [Caching Strategies](../../hld-building-blocks/caching-strategies.md)) — a `click_id` older than that has already been either counted or permanently dropped, so there's no correctness reason to remember it any longer, keeping the cache's size bounded by lateness tolerance rather than total click volume.

**8. Would a single global watermark work, or does each ad need its own?**
Watermarks are tracked per Kafka partition (and therefore, since partitioning is by `ad_id`, effectively per ad or per group of ads) rather than one global value — a burst of late events for one ad shouldn't stall window finalization for every other ad's completely unrelated, on-time stream.

**9. Why is click ingestion a separate pipeline from ad-serving, instead of the ad-serving service just writing the count directly?**
Because the two have opposite load profiles: ad-serving must answer in single-digit milliseconds no matter what, while ingestion has to absorb bursts up to 1M events/sec that are completely fine to process a few minutes late. Making ad-serving responsible for durably counting a click would mean a click-processing slowdown could degrade ad-serving's own latency — exactly the coupling the pipeline separation in Module 01 exists to avoid.

**10. How would you extend this to aggregate by multiple dimensions at once — say, by campaign and by geography, not just by ad?**
Add additional aggregation keys computed from the same event at ingestion time, each maintained by its own `WindowAggregator` instance behind the same interface (cross-ref Module 02's LLD) — the dedup check still only needs to happen once per `click_id`, since "have I seen this click" doesn't change based on how many different ways the click's count gets sliced afterward.
