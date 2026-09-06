# Module 00 — Overview

## Requirements

**Functional:**
- Follow / unfollow another account.
- Post a text or image update.
- View a feed of posts from accounts you follow, newest activity first.
- Like and comment counts visible on each post.

**Non-functional** (stated as assumptions, interview-style):
- 300M DAU, average user follows 300 accounts.
- A small fraction of accounts are "celebrities" with 10M+ followers — this is the case that drives most of this design.
- Feed-load latency target: p99 under 300ms.
- A post doesn't need to appear in every follower's feed instantly — a few seconds of lag is fine; it does need to appear *eventually*, for everyone, without a fan-out job silently dropping a follower.

That fraction of celebrity accounts is small in count but not in consequence: a single post from one of them is the single most expensive event this system handles, and the entire high-level design below exists mainly to keep that one event from being as expensive as it looks at first glance.

## Capacity Estimation

Using this guide's [back-of-envelope method](../../foundations/back-of-envelope-estimation.md):

- **Posts/day:** assume 10% of DAU posts once/day → 30M posts/day.
- **Posts/sec, average:** 30M / 86,400 ≈ 350/sec. Posting itself is never the bottleneck here.
- **Feed-reads/sec:** assume every DAU opens their feed 5x/day → 1.5B reads/day ≈ 17,000/sec average, **~50,000/sec at peak** (3x). Reads outnumber writes by roughly **50:1** — the number that decides almost everything below, the same way a 100:1 read:write ratio decided this guide's [URL Shortener](../url-shortener/README.md) HLD.
- **Storage/day:** ~2KB/post (text + metadata, images stored separately per [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md)) × 30M ≈ 60GB/day of post metadata — small. The number that actually matters is the next one.
- **The celebrity fan-out number:** one post from a 10M-follower account, fanned out to every follower's feed at write time, is **10M individual writes for one post**. Compare that to a normal user's post — 300 average followers — and the three-order-of-magnitude gap between "normal post" and "celebrity post" is the entire reason this design can't use one uniform strategy for both.

## Approach Walkthrough

Before any boxes: opening the feed should feel instant — reading a small, already-assembled list, not computing anything on the spot. Posting should feel instant too, from the poster's point of view, even though follower delivery can trail behind by a couple of seconds. Reconciling "reads must be cheap" with "one post can have 10 million followers" is the one real design problem here; everything below is infrastructure built to make both true without either bankrupting the other.

## API Surface

- `POST /posts` — `{ author_id, body, media_url? }` → `{ post_id, created_at }`.
- `GET /feed?cursor=&limit=` — a page of the caller's assembled feed, newest first.
- `POST /users/{id}/follow` / `DELETE /users/{id}/follow` — follow/unfollow.
- `POST /posts/{id}/like` — increments a denormalized like counter (see Database Design).
