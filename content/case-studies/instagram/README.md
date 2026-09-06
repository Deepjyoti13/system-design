# Design Instagram

![One upload, transcoded once, feeding both the feed's fan-out and the story's 24h-TTL fan-out](diagrams/hld.svg)

## Requirements

Post a photo or video with a caption, follow other accounts, see a feed of posts from followed accounts, view stories that disappear after 24 hours, like and comment.

## Two of the hardest problems are already solved elsewhere in this guide

The main feed is the exact fan-out-on-write vs. fan-out-on-read problem this guide's [News Feed System](../news-feed-system/00-overview.md) case study already works through in full, celebrity accounts included. Stories are the exact TTL-based expiry problem this guide's [Ephemeral Content](../../ephemeral-content-stories/00-overview.md) deep dive already covers — the partition-drop cleanup, the viewer-list tracking, all of it. This page doesn't re-derive either. It covers what's actually specific to Instagram: media-first posts at this scale, and how the feed and stories pipelines share infrastructure instead of being two unrelated systems.

## What's specific here

- **Media-first, not text-first.** Every post is an image or video before it's anything else — cross-ref [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md)'s pre-signed-upload pattern for getting the bytes off the critical path, and [CDN](../../hld-building-blocks/cdn.md) for why cached media is the textbook case for edge caching. Each upload gets transcoded into several sizes (thumbnail, feed-size, full-resolution) — the same idea [Video Streaming Service](../video-streaming-service/README.md) applies to video bitrates, just applied to images and done once per upload instead of continuously.
- **One upload, two fan-outs with two different lifetimes.** A post's feed entry and a story's fan-out entry both ride the same follow-graph and the same fan-out infrastructure — there's no reason to build it twice. What differs is retention: a feed fan-out entry persists; a story fan-out entry carries a 24-hour TTL and disappears the same way [Ephemeral Content](../../ephemeral-content-stories/00-overview.md) already describes. This is reusing one piece of infrastructure for two different lifetimes, not maintaining two separate systems that happen to look similar.
- **Likes and comments are their own high-write-volume counter path.** Cross-ref [Counting a Billion Likes](../../like-counting-at-scale/00-overview.md) directly rather than re-deriving the sharded-counter mechanism here — the problem and the fix are identical regardless of which app is counting the likes.

## Interviewer follow-ups

**Would a story appear in the main feed's fan-out infrastructure or a separate one?**
The same one. Both write a fan-out entry through the same follow-graph lookup and the same fan-out worker pool from [News Feed System](../news-feed-system/00-overview.md); the only difference is the story's entry is written with a TTL and the feed's isn't. Building a second fan-out pipeline just for stories would duplicate the exact infrastructure that already exists to solve "get this to all my followers."

**What happens if a user tries to view their own post immediately after posting, while it's still transcoding?**
Serve the original uploaded resolution (already durably stored) until the transcoded feed-size and thumbnail variants finish and become available — the poster sees their post immediately, at a size that costs more bandwidth than ideal for one view, rather than waiting on a pipeline that can genuinely take longer than the video itself for high resolutions.

**Why might Instagram's feed ranking be less strictly chronological than this guide's generic News Feed example?**
Because engagement-based ranking is itself a product decision layered on top of the fan-out mechanism, not a change to it — [News Feed System](../news-feed-system/00-overview.md) already notes a ranking service sits between "here are the candidate posts" and "here's what's shown," and that service can reorder by predicted engagement instead of recency without touching how the candidates were fanned out in the first place.
