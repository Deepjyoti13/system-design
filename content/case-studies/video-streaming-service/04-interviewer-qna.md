# Module 04 — Interviewer Q&A

**1. What happens when two requests hit the same resource at the same instant — specifically, thousands of viewers requesting the same just-uploaded segment from a cold CDN edge?**
Request coalescing at the edge: the first request triggers a single fetch to origin, and every other concurrent request for that exact segment waits on that same in-flight fetch rather than each triggering its own — origin sees one request, not thousands, and every viewer gets served once it lands.

**2. What happens when traffic spikes 10x for an hour — say, a globally-watched live event driving viewers to on-demand replays afterward?**
The CDN absorbs almost all of it, since playback is a cache-hit-dominated workload that doesn't touch origin or the transcoding tier at all; the actual constraint that could bite is upload volume (if the event also drives a burst of new uploads), which queues against the Transcoding Worker Pool and autoscales on queue depth rather than affecting anyone already watching.

**3. Why encode multiple resolutions instead of picking one "good enough" quality?**
Because "good enough" isn't one number — a 4K-TV viewer on fiber and a phone on a train's 3G connection have requirements that don't overlap. A single middle-ground quality means overpaying in bandwidth for the first viewer while still stalling for the second; multiple tiers cost more storage but let each viewer's own player pick correctly for their own conditions.

**4. Why chunk each rendition into short segments instead of one file per quality?**
Without independently-requestable segments, switching quality mid-playback means restarting the download from the beginning at the new bitrate. With segments, the player just requests the *next* segment at a different quality — the switch happens at a chunk boundary, invisibly to the viewer.

**5. Why does a rendition only appear in the manifest once ALL its segments are uploaded, rather than as soon as the first one is ready?**
Because the player has no way to distinguish "this segment doesn't exist yet" from "this segment will never exist" — a manifest advertising a rendition before it's fully written would 404 mid-playback the first time the player requests a not-yet-uploaded segment.

**6. Why is there no `segments` table in the schema?**
Because nothing ever queries a segment individually — its location is a computed object key from the video ID, quality, and segment index. Storing one row per segment would mean thousands of rows per video for zero query benefit; a `segment_count` and a URL pattern on the `renditions` row is all the Manifest Service needs.

**7. What happens if a transcoding worker crashes halfway through encoding a rendition?**
Nothing observable to any viewer — segments are written to a staging key, only promoted to the manifest-visible key on full success. A crashed job simply never promotes, and the at-least-once queue redelivers it to another worker, which safely re-runs the same (idempotent) work.

**8. How would you handle a live stream differently from this on-demand design?**
Transcoding can't happen ahead of time — each segment has to be encoded in near-real-time as the stream is produced, at every bitrate simultaneously, and pushed to the CDN with a short TTL moments after creation rather than the cache-forever pattern static, content-hashed assets use. This trades real end-to-end latency (typically several seconds behind the true live moment) for the "encode once, distribute forever" luxury on-demand video gets.

**9. Would you shard the metadata schema by `video_id`, the way a typical write-heavy case study in this guide might shard by a hash?**
Yes, and for the same reason [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) always gives: `video_id` matches the query pattern that actually runs constantly (a video's own renditions), so it's the key to shard by — not an arbitrary hash chosen only for even distribution.

**10. Where does most of this system's actual storage cost live — the database, or somewhere else?**
Almost entirely in object storage, as encoded segments — the relational schema here is a thin metadata layer (which renditions exist and whether they're ready) over a much larger blob store. Scaling pressure on the database is comparatively light precisely because the real data was deliberately kept out of it.
