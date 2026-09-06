# Module 04 — Interviewer Q&A

**1. What happens when two requests hit the same resource at the same instant — specifically, a user's follower count being read (to pick a fan-out strategy) at the exact moment they cross the threshold from a new follower?**
Momentary inconsistency is harmless here: the maintained `follower_count` counter might read as either just-under or just-over the threshold depending on ordering, so the post either fans out or doesn't — a one-time, one-post ambiguity around the threshold, not a repeating bug, and cheap enough to tolerate rather than lock around.

**2. What happens when traffic spikes 10x for an hour (a major world event)?**
Reads dominate (50:1), so `GET /feed` traffic is what spikes hardest — Feed Assembly and Feed Cache are both stateless/horizontally scalable, so they absorb it directly; the actual risk is a spike in *posting* from many normal accounts simultaneously reacting to the event, which spikes fan-out jobs — the worker pool's queue is bounded and sheds oldest-first under [backpressure](../../scalability-resilience/backpressure-load-shedding.md) rather than growing unboundedly, trading slightly delayed fan-out for a queue that never falls over.

**3. Why not just always use fan-out-on-read and skip the complexity of a threshold?**
Because 300 average followees × 50,000 reads/sec means every feed load would pay for merging up to 300 authors' recent posts, live, every time — fan-out-on-write exists specifically to make the overwhelmingly common case (a normal-sized account posting) cheap to read, at the cost of a bounded number of writes that's fine for that case.

**4. Why not always use fan-out-on-write and just skip celebrity posts entirely if it's too expensive?**
Skipping is not an option — a celebrity's followers still need to see the post; the choice is only WHEN the work happens: 10M writes at post time (expensive, upfront, mostly wasted on followers who won't look soon) vs. a per-reader merge cost paid lazily, only for followers who actually open their feed.

**5. How would you rank a feed by engagement instead of strictly reverse-chronological?**
That's a scoring function applied at the Feed Assembly merge step — instead of `merge_by_timestamp`, sort candidates by a score combining recency and predicted engagement; it doesn't change the fan-out decision above at all, since ranking only touches how already-gathered candidates are ordered, not how they were gathered.

**6. Would you shard the `follows` table by `follower_id` or `followee_id`?**
Neither alone covers both hot queries — "who do I follow" (by `follower_id`) and "who follows this author" (by `followee_id`, needed for fan-out and for `follower_count`) run equally often; a common real answer is storing the edge twice, once per access pattern, rather than picking one sharding key and forcing the other query to scatter-gather.

**7. What happens to a user's feed the moment they follow someone new?**
Nothing retroactive happens to their Feed Cache — a newly-followed account's *past* posts don't backfill into the cache; the next feed read's merge step picks them up going forward (and, if the new followee is over-threshold, `PullMerge` naturally includes their recent posts on the very next read regardless).

**8. Is `feed_items` ever pruned?**
Yes — it only needs to hold enough recent post IDs to serve pagination a few pages deep; a background job trims each user's list to, say, the most recent 1,000 entries, since anything older is vanishingly unlikely to be paged to and would otherwise grow unboundedly for a long-lived account.

**9. How would you handle a user who follows an unusually large number of accounts — say 50,000, far above the 300 average?**
`PullMerge`'s cost model assumes a small, bounded number of oversized *followees* per reader, not a bounded number of followees overall — a power-follower breaks the assumption that a feed read only ever merges a handful of live queries. A real system caps how many followees' posts get merged per read (paginating or sampling the rest) or leans more heavily on precomputed fan-out even for this user's own reads, trading some staleness for the same predictable read cost everyone else gets.

**10. How would you delete a user's account, including scrubbing them from every follower's feed?**
Unlike a payments ledger's audit-forever requirement, there's no obligation to keep a deleted user's posts discoverable — deleting their rows from `posts` and letting `FeedAssembly` silently skip `feed_items` entries whose `post_id` no longer resolves is far cheaper than proactively scrubbing millions of scattered `feed_items` rows across every follower's cache. The stale references get cleaned up lazily, the next time each follower's list is pruned (Module 03) — the same "let harmless staleness self-correct" instinct this guide's [Counting a Billion Likes](../../../like-counting-at-scale/04-interviewer-qna.md) deep dive uses for its own GDPR deletion question.
