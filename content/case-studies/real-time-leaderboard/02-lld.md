# Module 02 — Low-Level Design

![Two retried score-update requests for the same match; the idempotency check makes the second one a no-op instead of double-counting](diagrams/lld.svg)

**Score-submit pseudocode:**
```
LeaderboardService.submitScore(leaderboard_id, user_id, match_id, delta):
    dedupe_key = f"{leaderboard_id}:{match_id}:{user_id}"
    if not DedupeStore.setIfAbsent(dedupe_key, ttl=24h):
        return currentRank(leaderboard_id, user_id)   # already applied; safe no-op

    new_score = SortedSet.increment(leaderboard_id, user_id, delta)   # atomic ZINCRBY
    EventLog.append(leaderboard_id, user_id, match_id, delta, now())   # durable, for replay
    return {rank: SortedSet.reverseRank(leaderboard_id, user_id), score: new_score}
```
The dedupe check and the increment are deliberately two separate steps against two different stores, not one transaction — losing the dedupe record after a successful increment (a cache eviction, say) only risks a rare double-count on a retried request, not a lost update, which is the safer failure direction for a leaderboard.

**Tie-breaking:** a sorted set alone breaks ties on insertion/member order, which usually isn't what a game wants (typically: whoever reached the tied score FIRST should rank higher). This is solved without any extra logic by encoding a single combined sort key — `sort_key = score * SCALE + (MAX_TIMESTAMP - achieved_at)` — so the structure's native ordering already reflects "higher score first, earlier achievement breaks ties," with no special-case comparison logic anywhere in the read path.

**Nearby-rank query:** `rank = SortedSet.reverseRank(id, user_id)`, then a single range read for `[rank-5, rank+5]` — one O(log N + 11) operation, not 11 separate rank lookups.
