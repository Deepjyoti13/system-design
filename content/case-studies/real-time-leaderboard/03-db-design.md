# Module 03 — Database Design & Scaling

![Durable event log (source of truth for replay) versus the season-end snapshot table (permanent record after the in-memory leaderboard resets)](diagrams/er.svg)

- **Event log:** `(event_id, leaderboard_id, user_id, match_id, delta, applied_at)`, append-only, indexed on `(leaderboard_id, applied_at)` for ordered replay (cross-ref [Database Indexing](../../database-design/database-indexing.md)) — this table, not the in-memory sorted set, is the durable source of truth.
- **Season-end snapshot:** `(leaderboard_id, season_id, user_id, final_rank, final_score)`, written once when a leaderboard resets — since the sorted set is memory-only and gets wiped/reset for the next season, this is the only PERMANENT record of "who won season 7," separate from the append-only event log which just accumulates forever.
- **Scaling past one shard's capacity:** a single wildly popular leaderboard (a global all-time board, not scoped to a season) can outgrow one node's memory or CPU even with per-leaderboard sharding — split it further into N sub-leaderboards by hashing `user_id`, each maintaining its own top-K independently, and merge the N already-sorted top-K lists at query time (a bounded K-way merge over N*K items, not a scan of all N shards' full membership).
