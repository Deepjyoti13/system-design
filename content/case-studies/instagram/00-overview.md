# Module 00 — Overview

![One upload, transcoded once, feeding both the feed's fan-out and the story's 24h-TTL fan-out](diagrams/overview.svg)

## The feature, with no infrastructure in it yet

A user posts a photo or video. Everyone who follows them should see it in their feed, ranked among everyone else they follow, within seconds — and if that same user posts a story instead, the exact same followers should see it too, except it has to vanish after 24 hours without anyone running a cleanup job by hand. The interesting constraint isn't storing media or reading a follow list; it's that **one action (post) has to reach potentially millions of followers instantly, and the delivery mechanism for "reach my followers" is identical whether the content disappears in a day or stays forever.** Building two unrelated pipelines for those two cases would duplicate the single hardest part of this system twice.

## Requirements

**Functional:**
- Post a photo or video with a caption; the post appears in every follower's feed.
- Post a story (photo/video) that every follower can view for 24 hours, then it's gone.
- Follow/unfollow accounts; view a ranked feed of posts from followed accounts.
- Like and comment on posts.

**Non-functional** (stated as assumptions, interview-style):
- 500M daily active users, 2B monthly active users.
- 100M feed posts/day and roughly 500M stories/day (stories are posted far more casually, close to one per active user).
- Feed reads dominate: each DAU refreshes their feed roughly 20 times/day.
- Follower counts are extremely power-law skewed — the median account has a few hundred followers; the largest accounts have 100M+. A design that's fine for the median account has to not fall over for the outlier.
- New feed content should reach a follower within a few seconds of posting — this isn't a hard-realtime system, but "my friend posted an hour ago and I still don't see it" is a bug.

## Capacity Estimation

Using this guide's [back-of-envelope method](../../foundations/back-of-envelope-estimation.md):

- **Feed reads:** 500M DAU × 20 refreshes/day ≈ 10B reads/day → **~115,000 reads/sec average**, ~350,000/sec at a 3x peak factor (evenings, viral news events). This confirms the read path has to be a cache/precomputed-store lookup, never a live join over the follow graph.
- **Post writes:** 100M posts/day → ~1,150 writes/sec average, ~5,750/sec peak.
- **Fan-out writes (the dominant cost):** at a median of ~150 followers/account, 100M posts/day × 150 ≈ 15B fan-out writes/day → **~173,000 writes/sec average** just to pre-populate feed_store — over 100x the raw post-write rate. This is the write-amplification problem this guide's [News Feed System](../news-feed-system/00-overview.md) case study covers in full; the number matters here mainly to justify why celebrity accounts (Architecture module) can't be allowed to multiply it further.
- **Media storage:** assuming a 70/30 photo/video split at ~200KB/photo and ~5MB/video, raw ingest is 70M × 200KB + 30M × 5MB ≈ 164TB/day, and transcoding into thumbnail/feed/full-res variants (cross-ref [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md)) pushes total stored bytes to roughly **1.4x that figure per day** before any CDN caching is considered.

## Approach Walkthrough

A post and a story are the same event at the infrastructure level: an upload lands in object storage, gets transcoded once, and triggers a single `post-created` event that two independent async consumers pick up — one writes a persistent feed_store entry for every follower (fan-out-on-write), the other writes the identical shape of entry into a story_store with a 24-hour TTL. The one exception that keeps the write path from collapsing under its own success: accounts above a follower-count threshold skip the per-follower fan-out entirely, and the read path merges their posts in live instead — a hybrid fan-out strategy, not a single fixed one.

## API Surface

- `POST /uploads/presign {media_type}` → `{upload_url, media_id}` — client uploads bytes directly to object storage (cross-ref [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md)'s pre-signed-upload pattern), keeping raw media off the request path of any application server.
- `POST /posts {media_id, caption}` → `{post_id}`
- `POST /stories {media_id}` → `{story_id}`
- `GET /feed?cursor=` → `{items: [...], next_cursor}`
- `GET /stories/following` → `{stories: [...]}` — only unexpired entries; an entry past its TTL simply isn't there anymore, no explicit delete call.
- `POST /posts/{id}/like`, `DELETE /posts/{id}/like`
- `POST /posts/{id}/comments {text}` → `{comment_id}`
- `POST /follows {followee_id}`, `DELETE /follows/{followee_id}`
