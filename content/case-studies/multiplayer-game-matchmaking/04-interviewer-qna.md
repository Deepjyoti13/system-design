# Module 04 — Interviewer Q&A

**1. What happens when two matchmaking pairings could both claim the same waiting player at the same instant?**
Match Formation resolves this with an atomic conditional `removeBoth` against the pool — only one pairing's claim can actually remove both players. The losing pairing simply finds its candidate already gone and continues scanning for the next compatible one; nothing errors, and no ticket gets stuck.

**2. What happens when demand spikes 10x for an hour (a major tournament driving a surge of "find match" clicks)?**
Matchmaking Service is stateless and scales horizontally like any tier in this guide. The Game Server fleet is the harder constraint: a spike in *new* matches is absorbed by adding instances, but a spike in *concurrent live games already in progress* can't be, since an existing game is pinned to whatever instance formed it — this design leans on pre-provisioned headroom for that case rather than assuming autoscaling reacts fast enough, per Module 01's Load Handling.

**3. Why does the acceptable rating window widen over time instead of staying fixed?**
A fixed narrow window gives a fair match but leaves a player in a thin population (an extreme rating, an off-peak hour) with no bound on wait time; a fixed wide window gives everyone a fast match even when a much closer one was available a few seconds later. Widening the window with wait time gets the close match when the pool actually supports it, and only sacrifices fairness once the alternative is an unacceptable wait — the trade-off is explicit, not accidental.

**4. How does the ELO rating actually update after a game?**
The expected score for player A is `E_A = 1 / (1 + 10^((R_B - R_A) / 400))`, a smooth function of the rating gap between the two players. After the result, A's new rating is `R_A' = R_A + K * (S_A - E_A)`, where `S_A` is 1/0.5/0 for a win/draw/loss and `K` controls how much one game can move the rating — larger for a new, provisional player whose initial rating is still a rough guess, smaller for an established one.

**5. Why is game-session routing sticky, when the rest of this guide defaults to stateless services behind a load balancer?**
Because the board being validated against has to live *somewhere*, and round-tripping to a shared external store on every single move would add latency to the one path that most needs to feel instant. Pinning both players' connections to the one instance that holds the board in memory is a deliberate, named exception — the same one [Long Polling, WebSockets & SSE](../../scalability-resilience/long-polling-websockets-sse.md) names for any stateful connection tier, using the routing mechanism [Consistent Hashing](../../hld-building-blocks/consistent-hashing.md) describes.

**6. What actually stops a modified or cheating client from submitting an illegal move, or claiming a win it didn't earn?**
The server, not either client, runs `ChessEngine.isLegalMove` against the current authoritative board before a move is ever applied or broadcast, and a game's result is only ever recorded from a terminal board state the server itself computed (checkmate, a resignation event, a timeout) — never from a client-reported outcome. This is the same never-trust-the-client stance this guide's payments case study takes toward money, applied here to game state.

**7. What happens if a player's connection drops mid-game — do they lose instantly?**
No. The Game Server keeps the authoritative board in memory and starts a bounded abandonment timer rather than forfeiting immediately; reconnecting within that window resumes the game exactly where it stood. Only a disconnect that outlasts the grace period is scored as an abandonment.

**8. Why persist moves as a separate append-only log instead of only keeping the in-memory board?**
The in-memory board is the fast path for the next legality check, not something that survives an instance crash. The Move Log is what a restarting instance replays to rebuild `board_state` from scratch, what makes spectating and post-game review possible, and the eventual permanent record of the game — the same "the in-memory structure is not the only copy" reasoning [Real-Time Leaderboard](../real-time-leaderboard/01-architecture-hld.md) applies to its durable event log behind the sorted set.

**9. Would you reuse Real-Time Leaderboard's sorted-set design for "top rated players" here, or build something new?**
Reuse it directly — serving a fast top-K or a specific player's rank is exactly the problem that case study already solves, and rebuilding it here would just be re-deriving the same answer. This case study's own depth belongs to matchmaking's widening-window search and the live game session's authoritative-server and reconnection concerns, not to leaderboard-serving mechanics.

**10. How would you shard the matchmaking pool and the live-game store, and why differently from each other?**
The matchmaking pool partitions by `time_control` first (a blitz player can never be matched from a classical-only pool) with rating buckets nested inside. `games` and `moves` shard by `game_id`, keeping one game's full history on one shard. `players` and `rating_history` shard by an arbitrary `player_id` hash, since a rating lookup is always single-player and has no locality requirement with anything else. Each dataset's shard key matches the query that actually runs constantly against it, not a single scheme applied uniformly for its own sake.
