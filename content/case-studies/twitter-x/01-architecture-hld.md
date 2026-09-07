# Module 01 — Architecture & High-Level Design

![A retweet re-running the fan-out decision a second time, against the retweeter's own follower count, plus the separate near-real-time search lane and the media pointer that never touches the tweet row](diagrams/hld.svg)

## Monolith vs. microservices

Search is pulled into its own service, separate from the timeline/feed tier this guide's [News Feed System](../news-feed-system/01-architecture-hld.md) already builds out, because it has a different contract on two axes at once: freshness and query shape. Feed delivery tolerates "a few seconds, occasionally more under backpressure" (per that case study's own Load Handling section); search cannot — a user searching a hashtag is usually looking for something that just happened, so the indexing pipeline behind `GET /search` is held to roughly a 2-second freshness target regardless of how the feed's own fan-out queue is behaving that moment. The query shape is different too: a feed read is "posts from people I follow," scoped to one user's follow graph; a search read is "posts matching this token, across the entire corpus," which needs a purpose-built inverted index (cross-ref [Search & Inverted Indexes](../../scalability-resilience/search-inverted-indexes.md)), not a relational table scan. Coupling that workload to the feed tier would mean either slowing feed reads down to search's indexing discipline or loosening search's freshness to the feed's — neither is acceptable given the two stated SLAs.

Retweets, by contrast, are deliberately **not** a new service pulled apart from posting — `RetweetService` is a thin layer that calls the exact same `FanoutStrategy` interface News Feed System's `PostService` already uses, just from a second call site with a different actor. Splitting it into its own microservice would duplicate the push/pull decision logic for no benefit; the interesting design problem here isn't a new component, it's that one existing interface gets invoked twice, independently, for the same piece of content.

## Building blocks

| Block | Role |
|---|---|
| **Post Service** | Accepts a new tweet, writes it durably (text inline, media as a `media_url` pointer only), and triggers the same `FanoutStrategy` decision News Feed System already defines, keyed on **the author's** follower count |
| **Retweet Service** | Writes a thin reference row (`retweeter_id`, `original_post_id`, `created_at` — no content copy) and triggers a **second, independent** `FanoutStrategy` decision keyed on **the retweeter's** follower count — the one piece of this design with no analog in News Feed System |
| **Fan-out worker pool / Feed Cache** | Reused wholesale from News Feed System — same push/pull mechanics, same per-user precomputed feed, regardless of whether the delivered item is a post or a retweet pointer |
| **Search Indexer** | Async consumer (via the same [transactional outbox](../../hld-building-blocks/transactional-outbox-cdc.md) pattern this guide uses elsewhere) tailing new posts and retweets into an inverted index, tuned to a tighter freshness SLA than the feed's own fan-out relay |
| **Media Upload + Object/Blob Storage** | A tweet with an attachment gets a pre-signed upload URL (cross-ref [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md)); the tweet row only ever stores the resulting pointer, never the bytes |
| **Rate Limiter** | Per-account posting throttle (cross-ref [Rate Limiting](../../hld-building-blocks/rate-limiting.md)), with the ceiling set by the account's trust tier rather than one fixed number for every poster |

## Per-path walkthrough

**Original tweet, text-only (write)** — `Client → LB → Rate Limiter (trust-tier check) → Post Service (write posts row, SAME transaction as accepting the request) → FanoutStrategy.deliver(author_id, post_id) [exactly News Feed System's mechanism] → Outbox row for search indexing (SAME transaction) → client response`. Nothing here differs from News Feed System's own write path except the outbox row feeding a second, faster-SLA consumer alongside the fan-out queue.

**Original tweet with media (write)** — `Client → Media Upload (request pre-signed URL) → Client uploads directly to Object/Blob Storage → Client → Post Service (write posts row with media_url pointer) → ... (rest identical to the text-only path)`. The upload itself never touches Post Service's own write path or database — by the time `POST /tweets` is called, the media already exists in blob storage and the tweet row only records where.

**Retweet path (write) — the one genuinely new path** — `Client → LB → Retweet Service (write thin retweets row, pointer to original_post_id) → FanoutStrategy.deliver(retweeter_id, original_post_id) — a SECOND, independent invocation of the SAME strategy interface, evaluated against the RETWEETER's follower count, with no knowledge of what decision the original post's own fan-out made → Outbox row for search indexing → client response`. If the retweeter is below the fan-out threshold, this is cheap, exactly like an original post from a small account. If the retweeter is a celebrity, this triggers full celebrity-scale delivery for content that may have never needed it the first time around — the entire reason this case study exists on top of News Feed System.

**Search path (async index + read)** — *Write side:* `Post/Retweet write → outbox row → Search Indexer (tails the outbox) → inverted index update`, held to its own ~2-second freshness target independent of the feed's fan-out lag. *Read side:* `Client → LB → Search Service (query inverted index, rank by recency + relevance) → paginated response`.

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Retweet storage | Thin reference row (`retweeter_id`, `original_post_id`, `created_at`) | A full duplicate copy of the original tweet's content | A duplicate can silently desync if the original is ever edited or deleted, and at 150M/day it would multiply text storage for zero new content; a pointer keeps the original post the single source of truth for its own content and counts |
| Retweet fan-out | A second, independent `FanoutStrategy` invocation keyed on the retweeter's follower count | Reuse whatever fan-out decision the original post already made | The original author's follower count says nothing about the retweeter's — a 300-follower post retweeted by an 8M-follower account needs celebrity-scale delivery the original post's own (correctly cheap) fan-out never triggered |
| Search freshness | Dedicated indexing pipeline with its own, tighter SLA (~2s) | Reuse the feed's own fan-out queue and its "a few seconds, sometimes more" tolerance | The feed's tolerance is calibrated to timeline reading, not to someone actively searching a hashtag because something just happened — that use case can't absorb the same slack a scrolled-past timeline post can |
| Media storage | Object/blob storage; tweet row holds only a `media_url` pointer | Store media inline (BLOB column) in the tweet row | Media outweighs tweet text by roughly 200x in this system's own capacity math; inlining it would force every replica of the tweet table to copy megabytes it never queries, the same reasoning Object/Blob Storage gives generally |
| Posting rate limit | Per-trust-tier ceiling (new/unverified vs. established/verified account) | One fixed rate limit applied to every account | A ceiling low enough to stop a spam account would also throttle a legitimate high-frequency news account; the limit has to be a property of the account's trust level, not a single global constant |

## Load Handling

- **Peak-vs-average tolerance:** the aggregate posting rate (~5,800/sec average) is an ordinary horizontal-scaling problem for Post Service and Retweet Service, same as any stateless write tier. The real spike shape here is narrower and sharper than News Feed System's own celebrity-post case: a single retweet by an outsized account is one event injecting millions of fan-out jobs in the same second, not a smoothly elevated rate.
- **Where backpressure kicks in first:** the fan-out worker pool's queue absorbs a retweet cascade the same way News Feed System already describes for a celebrity's own post — except the retweet case can be *worse*, because it's not one predictable celebrity posting on their own schedule, it's any account's content suddenly inheriting celebrity-scale delivery the moment someone larger reshares it, with no warning built into the original post's own fan-out decision.
- **What gets shed under overload:** never a tweet, retweet, or like write. What can lag: fan-out delivery speed (same oldest-first shedding News Feed System already applies) and, distinctly, search-index freshness — an extreme burst can push the indexer briefly past its 2-second target, which is an explicit, monitored degradation rather than a silent one, never a dropped write.
- **Load-test target:** synthesize a single "celebrity retweets an ordinary tweet" event — one retweet from an 8M-follower account — and confirm the resulting fan-out burst (8M jobs enqueued within ~1 second) drains without elevating latency on the ordinary tweet-write path, while search indexing for that same event stays under its 2-second freshness target throughout.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| Two different users retweet the same original tweet at the same instant | Each retweet is its own row plus its own independent `FanoutStrategy` invocation, keyed by `(retweeter_id, original_post_id)` — there's no shared mutable state between the two retweets for either to race against | Both retweets succeed independently; neither retweeter's fan-out decision blocks or affects the other's |
| The same user double-taps retweet on the same post | Unique constraint on `retweets(retweeter_id, original_post_id)` — the second concurrent insert fails the constraint at the database level | The first retweet's result; the second is a no-op returning the same `retweet_id`, and fan-out fires exactly once |
| A user un-retweets while the original post's own fan-out (to a *different* follower) is still draining | Retweet deletion only removes the thin reference row; it doesn't and can't unwind fan-out jobs already enqueued from the *original* post — the same snapshot-semantics tolerance News Feed System already accepts for an unfollow racing a fan-out | A harmless, one-time staleness: the retweet may still transiently surface in some other reader's feed for a moment, self-correcting on the next read |
| A search query runs moments after a tweet is posted, racing the async indexer | The query simply doesn't see the new tweet yet if it lands before indexing catches up — eventually consistent, same category of guarantee as the feed, just tuned to a tighter SLA | The poster searching their own hashtag immediately after posting might not see it for up to ~2 seconds, self-correcting on the next query |

## Scaling & Reliability

- **Horizontal scaling:** Post Service, Retweet Service, and Search Indexer are all stateless and scale by request or job rate; the Fan-out worker pool and Feed Cache scale exactly as News Feed System already describes, since retweet-triggered jobs are indistinguishable from post-triggered ones once they reach that tier.
- **Circuit breaker:** the Search Indexer's write into the inverted index is wrapped in a circuit breaker (cross-ref [Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md)) the same way News Feed System wraps its own fan-out-to-cache write — a struggling index tier degrades search freshness, not the tweet or retweet write path, which never depends on it.
- **Retries:** retweet fan-out jobs retry with the same idempotent set-add semantics News Feed System's fan-out already relies on — a retried job re-adds the same `post_id` to the same follower's cache, a no-op if it's already there.
- **Dead-letter queue:** a search-index job that fails repeatedly (a malformed payload, a permanently broken shard) lands in a DLQ rather than blocking indexing for every other tweet queued behind it on that partition — same discipline this guide's other case studies apply to their own outbox relays.
- **Graceful degradation:** if the Search Indexer falls behind or goes down, `GET /search` serves whatever's already indexed — stale, never broken — and posting or retweeting is entirely unaffected, since indexing is strictly downstream and asynchronous of the write path.
- **Multi-region:** not built here — named as a real gap below.

## What you'd revisit as this grows

- **Cascading retweets** (a retweet of a retweet). This design always resolves `original_post_id` back to the true original, never chains through an intermediate retweet — worth stating explicitly, since chaining would make fan-out ambiguous about which follower count actually governs delivery at each hop.
- **Trending-hashtag read load on Search**, which can spike its *query* volume as hard as the retweet cascade that created the content spikes the *write* side — a caching layer in front of the hottest current queries is a natural addition this module doesn't build.
- **Ranking search results beyond recency + relevance** (personalization, engagement weighting) — deliberately scoped out here, the same way News Feed System scopes ranking-beyond-chronological out of its own module.
- **Trust tiers evolving into a continuous reputation score** rather than a small fixed number of rate-limit tiers, the same direction this guide's [Rate Limiting](../../hld-building-blocks/rate-limiting.md) page names generally.
