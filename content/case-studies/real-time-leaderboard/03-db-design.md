# Module 03 — Database Design & Scaling

![Durable event log (source of truth for replay) versus the season-end snapshot table (permanent record after the in-memory leaderboard resets)](diagrams/er.svg)

## Entities

- **Event log:** `(event_id, leaderboard_id, user_id, match_id, delta, applied_at)`, append-only, indexed on `(leaderboard_id, applied_at)` for ordered replay (cross-ref [Database Indexing](../../database-design/database-indexing.md)) — this table, not the in-memory sorted set, is the durable source of truth.
- **Season-end snapshot:** `(leaderboard_id, season_id, user_id, final_rank, final_score)`, written once when a leaderboard resets — since the sorted set is memory-only and gets wiped/reset for the next season, this is the only PERMANENT record of "who won season 7," separate from the append-only event log which just accumulates forever.

## Indexes — including the one that isn't a database index at all

The sorted set itself IS the index this system's hottest queries need — its internal skip-list structure keeps every member ordered by score at all times, which is precisely what makes `ZREVRANGE` (top-K) and `ZREVRANK` (a user's position) O(log N + K) and O(log N) operations instead of a sort computed at query time. There is no separate "index" to build or maintain on top of it; the ordering IS the data structure, always current, because every write goes through the same atomic increment that maintains it.

The durable stores behind it need conventional indexes for their own access patterns:

- `event_log(leaderboard_id, applied_at)` — the replay path's only query is "every event for this leaderboard, in order since some point" — this composite index serves that directly, leftmost-prefix on `leaderboard_id` then range-scanning `applied_at`.
- `season_snapshot(leaderboard_id, season_id)` as the natural primary key — a season's final standings are always looked up as a whole, never filtered further.

## Consistency

- **The sorted-set store:** strongly consistent per key, per shard — a rank or top-K query is never a "sort of correct" read against partially-applied increments, because every increment is atomic and the ordering it maintains is a direct consequence of that atomicity, not a separately-computed or eventually-converging value.
- **The read-through cache:** eventually consistent by explicit design, with a bounded (1-2s) staleness window — this is a deliberate, stated trade (Module 01's Load Handling), not an accident of the architecture. A user's OWN score-submit response never goes through this cache; only OTHER users' concurrent top-K reads can observe a few-second-old snapshot.
- **The durable event log:** strongly consistent at write time (appended in the same logical step as the increment), but read only during replay — its own read pattern has no latency requirement remotely close to the sorted-set store's, since it's never on the hot path.

## Scaling past one shard's capacity

A single wildly popular leaderboard (a global all-time board, not scoped to a season) can outgrow one node's memory or CPU even with per-leaderboard sharding — split it further into N sub-leaderboards by hashing `user_id`, each maintaining its own top-K independently, and merge the N already-sorted top-K lists at query time (a bounded K-way merge over N*K items, not a scan of all N shards' full membership). Note what this trades away: a user's *exact* global rank now requires querying every sub-shard rather than one O(log N) lookup — worth being explicit about, rather than claiming the sharded version is a free upgrade.

## Connecting it back

Look at all three modules together now: Module 00/01's "reads vastly outnumber writes, and staleness of seconds is fine" is why the read-through cache exists at all; the requirement that a retried score update must never double-count is why the idempotency key shows up here as a dedupe-store check ahead of the increment rather than a database constraint (there's no natural unique-constraint equivalent on an in-memory sorted set); and the fact that the sorted set lives only in memory is exactly why the durable event log exists as a completely separate store with its own consistency guarantees. Nothing in this design is arbitrary — every store, and every consistency choice attached to it, traces back to a requirement named in Module 00.
