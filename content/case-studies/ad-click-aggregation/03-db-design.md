# Module 03 — Database Design & Scaling

- **Aggregate store:** `(ad_id, window_start) -> {click_count, unique_click_count, finalized_at}`, sharded by `ad_id` — every serving query is "this ad's counts over some time range," so keeping one ad's windows together avoids fan-out on the dominant read.
- **Dedup cache:** a short-lived, TTL-based key-value store (cross-ref [Caching Strategies](../../hld-building-blocks/caching-strategies.md)) keyed by `click_id` — it only needs to retain entries slightly longer than the allowed-lateness window, not forever, since a click older than that has already either been counted or dropped for good.
- **Raw click log:** append-only, cold storage (cross-ref [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md)), kept for audit/fraud-review purposes — never read on the serving path, only by offline analysis.
