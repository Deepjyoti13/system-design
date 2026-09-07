# Module 04 — Interviewer Q&A

**1. How does a retweet's fan-out differ from an original post's?**
An original post fans out (or doesn't) based on *its author's* follower count, via `PostService` calling `FanoutStrategy.deliver()`. A retweet calls the exact same interface a second time, but keyed on *the retweeter's* follower count — a completely independent decision. A 300-follower account's tweet fans out cheaply; if an 8M-follower account retweets it minutes later, that retweet alone triggers celebrity-scale delivery the original post's own fan-out never needed and never predicted.

**2. Why store a retweet as a thin reference row instead of copying the tweet's content?**
Because the copy would need to stay in sync with the original forever — an edit or deletion of the source tweet would leave every duplicate silently wrong — and at 150M retweets/day, duplicating content would roughly double text storage for zero new information. A pointer (`retweeter_id`, `original_post_id`, `created_at`) keeps the original post the single source of truth for its own content and counts, the same "one fact, one place" reasoning this guide's [Normalization & Schema Design](../../database-design/normalization-schema-design.md) page makes generally.

**3. Walk through what happens when a celebrity retweets an account with only 300 followers.**
The original post already fanned out (or didn't) at post time, purely on the original author's 300 followers — cheap, done, unrelated to what happens next. The retweet is a new write: `RetweetService` inserts the thin reference row, then calls `FanoutStrategy.deliver(retweeter_id, original_post_id)` — and because the retweeter has 8M followers, that call resolves to `PullMerge` or triggers full push-fanout at celebrity scale, depending on where the threshold sits, entirely independent of the small, already-completed fan-out the original post ran through. The same content now has two, unrelated delivery histories.

**4. Why does search need a tighter freshness SLA than the timeline, given both are async and eventually consistent?**
Because the two serve different intents. Timeline lag of "a few seconds, sometimes more under backpressure" is invisible on a scrolled-past feed. Search is usually run because something just happened — a breaking hashtag, a live event — so a few extra seconds of staleness is directly visible and directly the point of the query. That's why Search Indexer runs its own outbox lane with its own ~2-second target, rather than riding on the feed's fan-out relay and its looser tolerance.

**5. How would you handle attaching an image or video to a tweet without slowing down the post write path?**
The client requests a pre-signed upload URL from `MediaStore.presignUpload()` and uploads the media *directly* to object storage, before ever calling `POST /tweets`. By the time Post Service runs, the media already exists and the tweet write only records a `media_url` pointer — a small, fast write regardless of whether the attachment is a 200KB image or a 50MB clip.

**6. How would you rate-limit posting without blocking a legitimate high-frequency account, like a news outlet?**
A single fixed ceiling can't serve both cases: low enough to stop a spam account, it also throttles a legitimate high-volume poster; high enough for the news account, it stops limiting spam at all. The fix is making the ceiling a property of the account's trust tier (cross-ref [Rate Limiting](../../hld-building-blocks/rate-limiting.md)) — new/unverified accounts get a low ceiling, established/verified accounts get a materially higher one — rather than one number applied uniformly.

**7. What happens if the original tweet is deleted but retweets of it still exist?**
`retweets.original_post_id` no longer resolves against `posts`. Feed assembly (per News Feed System's `FeedAssembly`) already skips a `post_id` it can't resolve, so the retweet silently disappears from any follower's feed rather than erroring or displaying broken content — no proactive cleanup of scattered `retweets` rows is needed.

**8. What happens if two different users retweet the same tweet at the same instant, or the same user taps retweet twice?**
Two different users: no race at all — each retweet is an independent row and an independent `FanoutStrategy` call, with no shared mutable state between them. The same user twice: the unique constraint on `retweets(retweeter_id, original_post_id)` means the second concurrent insert fails at the database level, and the handler returns the first attempt's `retweet_id` rather than creating a duplicate or firing fan-out a second time.

**9. How would you rank search results — recency vs. relevance — and does that change how the index itself is built?**
That's a scoring function applied over already-matched candidates — the same recency-plus-relevance blend News Feed System uses for feed ranking — and it doesn't touch how the inverted index is built or kept current. The indexer's job is purely "make this token findable, fast"; ranking is a read-time concern layered on top of whatever candidates the index returns.

**10. Would you shard `retweets` by `retweeter_id` or `original_post_id`?**
By `original_post_id`. The highest-load query this table ever sees is exactly the scenario this case study centers on — a viral post's retweet count and retweet list, hammered during a celebrity-driven cascade — and keeping all of one post's retweets on one shard avoids a scatter-gather at the worst possible moment. A user's own retweet history (for their profile) is comparatively low-volume per request and is served by a secondary index instead, the same asymmetric-access-pattern reasoning [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) gives for picking a shard key from the query that actually dominates, not from a desire for even distribution.
