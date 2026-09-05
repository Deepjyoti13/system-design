# Design a Video Streaming Service

![Upload to transcoding to multi-bitrate segments to CDN, with the player adapting quality between chunks](diagrams/hld.svg)

## Requirements

**Functional:** a creator uploads a video; the system processes it for playback; viewers stream it back on whatever device and network they happen to have.

**Non-functional** (stated as assumptions, interview-style): tens of millions of hours watched per day, playback should start within ~2 seconds of hitting play, and a viewer on a typical mobile connection should never see the spinning buffer wheel mid-video if the design is doing its job.

## Upload and transcoding happen off the critical path, on purpose

A raw upload goes straight to object storage via a pre-signed URL — this guide's [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md) page already makes the case for why the API server has no business sitting in that byte stream, and it applies here exactly as written.

Once the raw file lands, a transcoding job (queued — cross-ref [Message Queues & Pub/Sub](../../hld-building-blocks/message-queues-pubsub.md)) converts it into **several** resolutions and bitrates — say 240p, 480p, 720p, 1080p — because at upload time nobody knows what network or screen every future viewer will have. This has to happen asynchronously: transcoding is CPU-heavy enough that producing the higher resolutions can take longer than the video's own runtime, and a creator publishing a video can't be blocked waiting on it.

## Adaptive bitrate streaming is the actual playback mechanism

The naive design — pick one resolution and stream it — either wastes bandwidth for viewers who could handle more, or stutters for viewers who can't handle what was picked. Real streaming systems instead chunk each resolution into short segments (2–10 seconds each) and hand the player a **manifest**: a list of every available quality, and where to find its segments. The player measures its own current download speed and requests the next segment at whatever quality currently fits — upgrading or downgrading between segments as conditions change, not committing to one quality for the whole video.

The reason chunking specifically matters: without independently-requestable segments, switching quality mid-playback would mean re-downloading from the start of the file at the new bitrate. With segments, the player just requests the *next* segment at a different quality — the switch happens at a chunk boundary, invisibly.

## Where the CDN carries almost the whole load

Video segments are about as good a caching case as exists in this entire guide — cross-ref [CDN](../../hld-building-blocks/cdn.md) directly. They're static (never change once encoded), and the *same* segment of a popular video gets requested by potentially millions of viewers. The RTT argument that page already makes applies here at its largest scale: a viewer in Mumbai should never fetch a segment from an origin in Virginia if a Mumbai edge PoP cached that exact segment from an earlier viewer's request a few seconds ago. In a well-run video service, the overwhelming majority of playback traffic never reaches origin storage at all — it's served entirely from CDN edges.

## Interviewer follow-ups

**How would the player decide when to switch quality up vs. down?**
It tracks recent segment download throughput (bytes received / time taken) and compares it against the bitrate of each available quality tier — if sustained throughput comfortably exceeds the next tier up's bitrate, it steps up; if a segment download is taking too long relative to the buffer remaining, it steps down immediately to avoid stalling. Buffer health (how many seconds of already-downloaded video are queued up) matters as much as raw throughput — a player with a healthy buffer can afford to try a higher quality and back off if it doesn't work out.

**Why not just transcode into one "good enough" quality instead of several?**
Because "good enough" isn't one number — a viewer on a fast home connection with a 4K TV and a viewer on a train's 3G connection have requirements that don't overlap, and picking a single middle-ground quality means overpaying in storage/bandwidth for the first viewer while still stalling for the second. Multiple tiers cost more storage but let each viewer's player pick correctly for their own conditions.

**How would you handle a live stream differently from an on-demand video?**
Transcoding can't happen ahead of time — each segment has to be encoded in near-real-time as the stream is produced, at multiple bitrates simultaneously, and pushed to the CDN moments after creation with a short TTL (rather than the "cache forever, content-hashed" pattern [CDN](../../hld-building-blocks/cdn.md) uses for static assets). This adds real end-to-end latency (typically several seconds behind the true live moment) as the unavoidable cost of encode-then-distribute happening under time pressure instead of ahead of time.

**What happens to a viewer mid-playback if the CDN edge they're using has a cache miss on the next segment?**
That one segment fetch falls back to origin (or a mid-tier regional cache) exactly as described in [CDN](../../hld-building-blocks/cdn.md)'s cache-miss follow-up — the player's buffer is what absorbs this: as long as enough segments are already buffered ahead of playback position, one slow fetch doesn't surface as a stall to the viewer.
