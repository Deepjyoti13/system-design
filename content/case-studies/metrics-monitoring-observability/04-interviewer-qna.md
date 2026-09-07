# Module 04 — Interviewer Q&A

**1. What's the single hardest problem in this design — and why isn't it "can you ingest a million points a second"?**
Cardinality. Raw ingest volume is an ordinary horizontal-scaling problem for the stateless Intake Service and Stream Writer tiers — more partition consumers, same per-point logic. A metric's tag combinations multiplying into millions of distinct time series is what actually breaks storage cost and query latency, and it's the one failure mode more servers don't fix, per Module 00's "one bad tag" capacity estimate.

**2. How do you stop one badly-tagged metric — say, tagged by a raw user ID — from degrading the whole platform?**
The Series Catalog enforces a hard cardinality cap per metric name, checked atomically at the exact moment a brand-new series would be created. A tag combination that would exceed the cap is quarantined, with the reason recorded, rather than silently accepted — the same "make the rejection visible" instinct as Module 01's Scaling & Reliability section.

**3. Why not just store metric points in an ordinary relational table, one row per point?**
Module 00's capacity estimation makes the case with numbers: a generic row-per-point store for 15 days at this ingest rate is roughly 195 petabytes; a purpose-built TSDB using delta-of-delta timestamp and XOR value encoding stores the same data in roughly 1.9 terabytes, because a generic row store pays per-row overhead — a header, a B-tree index entry — that dwarfs a 16-byte timestamp/value pair, and can't exploit that one series' points arrive on a steady cadence and change slowly (Module 03).

**4. How would you compute an accurate p99 for "checkout-service across 200 hosts, grouped by region" from data that's already been rolled up into hourly buckets?**
Each rollup bucket stores a mergeable sketch — a t-digest — instead of a precomputed percentile number. Merging 200 hosts' sketches for the same hour, then computing the percentile once from the merged result, is statistically correct in a way averaging 200 already-computed p99s is not (Module 03).

**5. Push-based agents (Datadog agent, StatsD) or pull-based scraping (Prometheus)? Why?**
Push, because the fleet churns constantly — autoscaling, redeploys — and a puller would need to continuously rediscover scrape targets. Push means every host owns its own delivery, at the cost of pushing retry and backoff logic onto every agent instead of centralizing it in one scraper (Module 01's trade-off table).

**6. What happens if a host's agent can't reach the Intake Service for 20 minutes during a network partition?**
The agent buffers locally in a bounded queue and drops its *oldest* points once that buffer fills — an explicitly accepted, named lossy behavior per Module 00's non-functional requirements, unlike this guide's [payments case study](../payments-system/00-overview.md), where a request is never silently dropped.

**7. How do you keep an alert from flapping on a metric bouncing right at its threshold?**
The Alerting Engine requires a configurable number of consecutive breaching ticks before flipping a monitor's state — hysteresis. It's the same state-machine discipline this guide applies to the payment and job-scheduler state machines, just tuned here for noise tolerance instead of transactional correctness (Module 02).

**8. Why evaluate alerts against a separately-refreshed aggregate instead of re-running the rule on every raw point as it's ingested?**
Evaluating on every raw point scales with (active monitors x ingest rate), unbounded on both axes. Pulling each monitor's own short window every 10-30 seconds instead bounds the cost to (active monitors x tick rate), trading a small, already-accepted detection delay for a cost the system can actually sustain (Module 01's trade-off table).

**9. How would you shard the TSDB — and why not by time range, which seems like the natural fit for time-series data?**
By `series_id` hash, not time. Sharding by time range would put every currently-arriving point on whichever shard owns "now" — the identical hot-shard failure mode [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) describes for a monotonically increasing key. Hashing spreads both write load and total storage evenly, at the cost of a multi-series query needing to fan out across a handful of shards instead of hitting one (Module 03).

**10. How does a query like "p99 latency for checkout-service, grouped by region" actually resolve which time series to read?**
The tag filter `service:checkout` is a lookup into `series_tags`, an inverted index mapping `tag_key, tag_value → series_id`, structurally identical to how a search engine resolves a term to a posting list of documents (cross-ref [Search & Inverted Indexes](../../scalability-resilience/search-inverted-indexes.md)). The resulting series are then grouped by their `region` tag, and each group's sketches are merged before the final percentile is computed.
