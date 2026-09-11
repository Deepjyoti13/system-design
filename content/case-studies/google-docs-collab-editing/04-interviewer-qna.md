# Module 04 — Interviewer Q&A

**1. Two users type in the same spot at the exact same millisecond — walk me through what actually happens.**
Both operations reach the Document Session Service with the same `base_seq`. The service applies the first one it processes, appends it to the log, then transforms the second against that now-appended operation before applying it — the second user's edit lands adjusted for what changed underneath it, never overwritten and never rejected.

**2. Why Operational Transformation instead of CRDTs, which don't need a central sequencer at all?**
Because this design already accepts sticky per-document routing to one Document Session Service instance (Module 01's Building Blocks), which makes a central sequencer free rather than a bottleneck to avoid — CRDTs' main advantage is removing that requirement, which isn't a constraint this system has. In exchange, OT keeps the document a plain, compact character sequence instead of a CRDT's heavier per-character metadata that often doesn't fully compact back down.

**3. What happens if the Document Session Service instance handling a hot document crashes mid-session?**
Nothing in the operation log is lost — every applied operation was already durably appended before being broadcast (Module 02's error cases). Clients reconnect, a new instance is assigned ownership of the document, and each client catches up via the exact same `since_seq` snapshot-plus-replay path a normal reconnect uses; there's no special-cased crash-recovery logic.

**4. Why is cursor position broadcast live but never written to the operation log?**
Because it fails a cost/benefit test the log doesn't need to pass for real edits: cursor position is high-frequency, has no correctness requirement (a stale or lost cursor is invisible, not silently-wrong data), and would inflate the log's write volume for zero durability benefit. The operation log exists specifically to guarantee edits are never lost — a guarantee cursor position never needed in the first place.

**5. If a client's connection lags for 30 seconds, does its next edit apply against a stale view of the document?**
No — the transform walk in `handleIncoming` (Module 02) replays every operation between the client's `base_seq` and the log's current head before applying the incoming edit, regardless of how far behind that is. The mechanism doesn't degrade with lag, only the amount of transform work per operation grows.

**6. Why bound replay with periodic snapshots instead of just replaying the full operation log on every reconnect?**
Because Module 00's capacity math shows the log growing at roughly 1.3TB/hour at peak — replaying an old, heavily-edited document's entire history on every reconnect would make catch-up latency grow unboundedly with document age. A snapshot bounds replay to "since the last snapshot," and because it's just a point-in-time read, it never blocks or is blocked by the live edit stream (Module 01's Concurrent-User Handling).

**7. Would you ever accept eventual consistency here, the way this guide's [Counting a Billion Likes](../../../like-counting-at-scale/00-overview.md) case study does for a like count?**
Not for the operation log itself — an edit silently applying a few seconds late in the wrong order is exactly the "silently lost or overwritten" failure Module 00 rules out entirely. The one place this design *does* accept looser consistency is the `documents.latest_seq` metadata pointer and snapshot freshness (Module 03), because neither of those affects what the document actually contains, only how quickly secondary views reflect it.

**8. How would you scale this to millions of concurrent documents rather than a few hot ones?**
By adding more Document Session Service shards, each owning a disjoint set of documents by `document_id` — this scales the *count* of documents the system handles, not the concurrency ceiling of any single hot document, since one document's operations still have to funnel through one instance in order (Module 01's Load Handling names this distinction explicitly).

**9. Why not just use optimistic locking — reject an edit if the document changed since the client last fetched it?**
Because that pushes the conflict back onto the user as a visible error ("someone else edited this, please retry"), which is precisely the UX this design is built to avoid — Module 00's bar is that both concurrent edits *survive*, not that one wins and the other gets rejected and has to be manually reapplied.

**10. What's the actual failure mode if the transform logic itself has a bug?**
Unlike a crash or a network blip, a transform bug doesn't fail loudly — it can produce a document state no single user's actual keystrokes would produce, which is the one failure category this whole design exists to prevent and can't recover from after the fact (Module 02's error cases call this out specifically, treating a nonsensical transform result as an alertable bug rather than something to clamp and silently continue past).
