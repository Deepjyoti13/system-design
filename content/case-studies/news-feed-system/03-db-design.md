# Module 03 — Database Design & Scaling

![Schema: users, posts, follows, feed_items](diagrams/er.svg)

**Entities:** `users` (id, follower_count — kept current, see denormalization below), `posts` (id, author_id, body, created_at), `follows` (follower_id, followee_id — the many-to-many join table), `feed_items` (user_id, post_id — the materialized fan-out-on-write output for sub-threshold authors).

**Two different sharding keys for two different tables, deliberately.** `posts` is sharded by `author_id`, so one author's own posts stay together — this guide's [sharding page](../../hld-building-blocks/data-partitioning-sharding.md) picks a shard key by asking "what does the busiest query actually filter by," and the busiest query against `posts` is "this author's recent posts," used constantly by `PullMerge` above. `feed_items`, by contrast, is sharded by `user_id` — its only real query is "this reader's precomputed feed," so keeping one reader's feed items together is what matters there, not which author wrote them. Sharding both by the same key would make one of those two queries fan out across shards for no benefit.

**Indexes:** `follows(follower_id)` (assembling "who does this user follow" on every feed read) and `follows(followee_id)` (computing follower counts, and the fan-out job's own "who follows this author" lookup) — both directions are hot, so both get an index rather than picking one. `posts(author_id, created_at)` for `PullMerge`'s "this author's recent posts" query.

**Denormalization:** `users.follower_count` is a maintained counter, not a live `COUNT(*)` over `follows` — it's read on every single post (to decide `PushFanout` vs. `PullMerge`) and would be one of the hottest queries in the whole system if computed live, the same "freeze a number that's expensive to recompute" call this guide's [e-commerce schema](../../database-design/ecommerce-schema-worked-example.md) makes for `total_amount`.
