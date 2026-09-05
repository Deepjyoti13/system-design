# Module 03 — Database Design

**Diagram for this module:** [Three Entities, One Denormalization](https://claude.ai/code/artifact/4c71e139-99e6-4391-b316-1e3eae00ae41)

## From entities to schema

Three entities fall out of module 01's requirements directly:

- **`users`** — who authors posts and who likes them.
- **`posts`** — the thing being liked; carries the denormalized, reconciled `like_count`.
- **`likes` (the ledger)** — one row per (user, post) that currently holds a like. This is the exact, synchronous source of truth module 00 promised.

## Per-entity storage choice, justified by query pattern

- **`users`, `posts` → relational (sharded Postgres).** Posts need a join to `users` for author display, and the kind of secondary-index queries a content platform always eventually wants (a user's own posts, moderation queries). This is the same justification the URL shortener used for its own relational choice.
- **`likes` → sharded NoSQL, partition key = `post_id`, clustering key = `user_id`.** The only two query patterns this table ever serves are "does (user, post) exist" and "list everyone who liked this post" — both single-partition operations, no joins. This is a genuinely different answer from the URL shortener's, and the reason is worth stating explicitly rather than defaulting to whatever the rest of the schema uses: **the query pattern picked the storage engine, not a house style.**

## Why the partition/clustering key *is* the uniqueness constraint

In a relational schema, "a user can only like a post once" needs an explicit `UNIQUE(user_id, post_id)` index maintained alongside the table. In the ledger, `post_id` as the partition key and `user_id` as the clustering key make that constraint free: two likes from the same user on the same post are, structurally, the same row. There's nothing to enforce because there's nothing else the key *could* mean. This is a direct trade for the join capability given up above — the ledger can't easily answer "every post a given user has liked" (that's a query against the wrong side of the partition key), and if that became a real requirement, it would need a second, inverted table keyed by `user_id`, not a index on this one. Naming that gap now, rather than discovering it during an incident, is the actual skill being tested.

## Why `like_count` is denormalized, and by whom

Exactly the same shape as the URL shortener's `click_count`: the fully-normalized answer to "how many likes does this post have" is `SELECT count(*) FROM likes WHERE post_id = ?` against the highest-write-volume table in the system — viable at low volume, not at 50,000 writes/sec on one partition. `posts.like_count` exists so a post render never has to run that count, and it is written by exactly one process: the Count Aggregator from module 01, in batches. Nothing else — not the toggle, not any read path — is permitted to write it. Naming *who owns a write* is as important as naming the column.

## Indexes

- `likes`: no additional index needed beyond the partition/clustering key structure itself — it already serves both the point-lookup and the "list likers" access pattern.
- `posts`: an index on `author_id` for "this user's posts"; `post_id` as the primary key already serves the hot path (rendering a single post).
- `users`: `user_id` primary key only; nothing else in this system's scope needs a secondary index on `users`.

## Consistency

- **`likes` (the ledger):** must be strongly consistent for a single (user, post) key — a user's own toggle can never appear to "flicker" between liked/not-liked on repeated reads. This is a per-key guarantee, not a cross-key one — nothing about this design requires strong consistency *across* different posts or different users.
- **`posts.like_count`:** eventually consistent by design, lagging the ledger's true count by up to the aggregator's batch window (~2-5s). This is stated as a target, not discovered as a bug.

## Scaling the schema

- **Sharding `likes`:** already partitioned by `post_id` from day one — a viral post's likes all land in one partition, which is exactly why the counter (Redis, not this table) is what absorbs the write-rate spike, not the ledger. The ledger's write rate per like is one conditional insert, considerably cheaper than an unconditional increment, but a genuinely record-breaking post could still make one partition hot; the same "detect and re-shard a hot key" problem from module 01 recurs here and isn't solved differently.
- **Sharding `posts`:** by `post_id`, matching how it's looked up everywhere else in the system.
- **Read replicas vs. sharding, again:** exactly the URL shortener's distinction applies — replicas solve read throughput on `posts` (post views vastly outnumber post creations), sharding solves the `likes` ledger's write volume and total size. Reaching for one when the other is the actual bottleneck is the mistake to avoid.

## Connecting it back

The chain holds across all three modules: the read:write ratio in module 00/01 is why a cache exists at all; the decision that the toggle must be exact and synchronous is why the ledger's partition/clustering key was chosen to make uniqueness free instead of enforced; and the choice to batch the durable count is why `like_count` is denormalized and single-writer. Nothing in this schema is arbitrary — every field, key, and index traces back to a requirement stated in module 01.
