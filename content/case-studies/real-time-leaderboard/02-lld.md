# Module 02 — Low-Level Design

![Two retried score-update requests for the same match; the idempotency check makes the second one a no-op instead of double-counting](diagrams/lld.svg)

## Interfaces vs. implementations

- **`LeaderboardStore`** *(interface)* → **`RedisSortedSetStore`** — `increment(leaderboardId, userId, delta)` (atomic `ZINCRBY`), `topK(leaderboardId, limit)` (`ZREVRANGE`), `rank(leaderboardId, userId)` (`ZREVRANK`), `range(leaderboardId, fromRank, toRank)` (for "nearby"). Every read and write in this module goes through this one interface — a future move to a different sorted-set implementation, or a sharding scheme change, touches only what's behind it.
- **`DedupeStore`** *(interface)* → **`RedisDedupeStore`** — `setIfAbsent(key, ttl)`, the entire idempotency mechanism behind one method.
- **`EventLog`** *(interface)* → **`AppendOnlyEventLog`** — `append(leaderboardId, userId, matchId, delta, timestamp)`, `replay(leaderboardId, sinceOffset)` — used only by the crash-recovery path, never by the hot read/write path.
- **`LeaderboardService`** — the orchestrator. Depends on all three interfaces, implements none of the storage itself.

## Score-submit pseudocode

```
LeaderboardService.submitScore(leaderboard_id, user_id, match_id, delta):
    dedupe_key = f"{leaderboard_id}:{match_id}:{user_id}"
    if not dedupeStore.setIfAbsent(dedupe_key, ttl=24h):
        return currentRank(leaderboard_id, user_id)   # already applied; safe no-op

    new_score = leaderboardStore.increment(leaderboard_id, user_id, delta)   # atomic ZINCRBY
    eventLog.append(leaderboard_id, user_id, match_id, delta, now())         # durable, for replay
    return {rank: leaderboardStore.rank(leaderboard_id, user_id), score: new_score}
```

The dedupe check and the increment are deliberately two separate steps against two different stores, not one transaction — losing the dedupe record after a successful increment (a cache eviction, say) only risks a rare double-count on a retried request, not a lost update, which is the safer failure direction for a leaderboard.

## Tie-breaking

A sorted set alone breaks ties on insertion/member order, which usually isn't what a game wants (typically: whoever reached the tied score FIRST should rank higher). This is solved without any extra logic by encoding a single combined sort key — `sort_key = score * SCALE + (MAX_TIMESTAMP - achieved_at)` — so the structure's native ordering already reflects "higher score first, earlier achievement breaks ties," with no special-case comparison logic anywhere in the read path.

## Nearby-rank query

`rank = leaderboardStore.rank(id, user_id)`, then a single range read for `[rank-5, rank+5]` — one O(log N + 11) operation, not 11 separate rank lookups.

## Concurrency at the code level

`leaderboardStore.increment(...)` needs no in-process lock, and this is worth stating explicitly: the Leaderboard Service runs on many horizontally-scaled instances, so a language-level mutex would only protect against other threads *on the same instance* — it would do nothing about another instance incrementing the same user's entry a moment later. Correctness comes entirely from the sorted-set store's own atomic, single-threaded command execution per key, the same pattern this guide applies everywhere two writers might touch the same row: push the atomicity requirement down into the one system that can actually provide it for free.

The one place an actual guard is needed is the `dedupeStore.setIfAbsent` check itself — but that guard lives inside the dedupe store's own atomic operation (a conditional set), not as a lock the service code manages. `LeaderboardService.submitScore` never holds anything resembling a lock; every safety property it has comes from composing two independently-atomic operations against two different stores.

## Design patterns you just used, named

- **Repository pattern** — `LeaderboardStore`, `DedupeStore`, and `EventLog` all hide storage behind method calls; `LeaderboardService` never issues a raw Redis or database command directly.
- **Strategy pattern** — the sort-key computation (`score * SCALE + (MAX_TIMESTAMP - achieved_at)`) is a swappable strategy behind the same `increment`/`rank` interface; a different tiebreak rule (say, "later achievement wins") is a one-line change to the key formula, not a rewrite of any query path.
- **Idempotent receiver** — `submitScore`'s dedupe-then-apply shape is this pattern by name: the same logical operation can be invoked more than once with the same net effect as invoking it exactly once, which is what makes the whole design safe under retries and at-least-once event delivery.

## Practice: extend it yourself

Before moving to Database Design, sketch (pseudocode is fine) how you'd add:

1. **A per-friend-group leaderboard** — "rank me only among my 200 friends," for every one of 50M users. Does this reuse the same sharded sorted-set approach directly, or does the cardinality (potentially one leaderboard per user, rather than a handful of global ones) push you toward a different structure entirely? What does `topK` even mean here if there's no single shared leaderboard to shard?
2. **A time-windowed leaderboard** ("this week's top players," rolling) alongside the existing all-time one. Does the existing `submitScore` call need to write to two sorted sets instead of one, and if a week boundary is crossed mid-match, which timestamp — match start or match end — decides which window's leaderboard gets the point?

Neither has one clean answer — the point is noticing which parts of the existing interfaces (`LeaderboardStore.increment`, the idempotency key shape) keep working unchanged, and which assumptions (one leaderboard ID, one sorted set per leaderboard) the new requirement actually breaks.
