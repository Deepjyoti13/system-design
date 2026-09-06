# Module 03 — Database Design & Scaling

![Schema: users, posts, follows, feed_items](diagrams/er.svg)

## From entities to schema

**Entities:** `users` (id, follower_count — kept current, see denormalization below), `posts` (id, author_id, body, created_at), `follows` (follower_id, followee_id — the many-to-many join table), `feed_items` (user_id, post_id — the materialized fan-out-on-write output for sub-threshold authors).

## Why `posts` and `feed_items` are sharded by different keys, deliberately

`posts` is sharded by `author_id`, so one author's own posts stay together — this guide's [sharding page](../../hld-building-blocks/data-partitioning-sharding.md) picks a shard key by asking "what does the busiest query actually filter by," and the busiest query against `posts` is "this author's recent posts," used constantly by `PullMerge`. `feed_items`, by contrast, is sharded by `user_id` — its only real query is "this reader's precomputed feed," so keeping one reader's feed items together is what matters there, not which author wrote them. Sharding both by the same key would make one of those two queries fan out across shards for no benefit.

## Indexes

- `follows(follower_id)` — assembling "who does this user follow" on every feed read.
- `follows(followee_id)` — computing follower counts, and the fan-out job's own "who follows this author" lookup. Both directions are hot, so both get an index rather than picking one.
- `posts(author_id, created_at)` — `PullMerge`'s "this author's recent posts" query, ordered by recency.
- `feed_items(user_id)` — the primary access pattern behind `GET /feed`'s fan-out-on-write portion; the table's own shard key typically doubles as this index.

## Denormalization: `users.follower_count`

`users.follower_count` is a maintained counter, not a live `COUNT(*)` over `follows` — it's read on every single post (to decide `PushFanout` vs. `PullMerge`) and would be one of the hottest queries in the whole system if computed live, the same "freeze a number that's expensive to recompute" call this guide's [e-commerce schema](../../database-design/ecommerce-schema-worked-example.md) makes for `total_amount`.

## Consistency

- **`follows`:** needs to be read-your-writes for the follower who just acted — a user who taps "follow" has to see that reflected immediately if they check their own following list, even if the follower-count *aggregate* elsewhere lags briefly.
- **`users.follower_count`:** eventually consistent by design — a maintained counter updated asynchronously off the follow/unfollow write; a brief lag between "I followed them" and the count reflecting it is invisible to anyone except the account being followed, and harmless even there.
- **`feed_items`:** eventually consistent, explicitly per Module 00's requirement ("a few seconds of lag is fine") — this is the entire premise the fan-out-on-write design leans on; making it strongly consistent would mean synchronous fan-out on every post, precisely the cost this design exists to avoid paying.
- **`posts`:** strongly consistent at write time (a post either exists or it doesn't, no ambiguous intermediate state) — but read replicas are a natural fit for `PullMerge`'s read-heavy "this author's recent posts" query, since a replica's small lag doesn't change *what* was posted, only how promptly it's visible to a pull-based read.

## Scaling the schema

- **`feed_items` is pruned, not left to grow unboundedly:** it only needs to hold enough recent post IDs to serve pagination a few pages deep; a background job trims each user's list to, say, the most recent 1,000 entries, since anything older is vanishingly unlikely to be paged to and would otherwise grow unboundedly for a long-lived account.
- **Storing the `follows` edge twice, once per access pattern:** rather than picking one sharding key for `follows` and forcing the other hot query ("who do I follow" vs. "who follows this author") to scatter-gather, a common real answer is denormalizing the edge into two tables, each shaped for one direction.
- **Read replicas vs. sharding, again:** replicas solve `posts`' read-heavy `PullMerge` query; sharding solves `feed_items`' total data size and per-user write volume. Reaching for one when the other is the actual bottleneck is the mistake to avoid, the same distinction this guide draws in every other case study's DB design.

## Connecting it back

Look at all three modules together now: Module 00's "reads must be cheap, one post can have 10 million followers" tension is why Module 01 splits delivery into push-vs-pull by follower count in the first place; that same split is why `posts` and `feed_items` are sharded by different keys here, each matching the one query that actually depends on it; and the eventual consistency Module 00 explicitly allows ("a few seconds of lag is fine") is what makes fan-out safe to do asynchronously at all, rather than forcing every post to pay for synchronous delivery. Nothing in this schema is arbitrary — every index and sharding choice traces back to the read/write asymmetry that drives this entire case study.
