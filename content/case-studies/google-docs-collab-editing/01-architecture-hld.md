# Module 01 — Architecture & High-Level Design

![Client WebSockets pinned to one Document Session Service instance per document, appending to an operation log and periodically snapshotting](diagrams/hld.svg)

## Monolith vs. microservices

The document-editing path is pulled out as its own service, never folded into a general Documents/Files monolith that also handles listing, sharing, and permissions. The reason is the operational profile, not a stylistic preference: this service holds **live, in-memory, per-document state** (the current text plus the transform machinery) for every actively-edited document, and every operation for a given document must be handled by the *same* instance, in order — a fundamentally different shape than a stateless request handler that can run anywhere. Folding that into a general-purpose service would mean an unrelated deploy or a listing-page traffic spike could evict or slow down live editing sessions that have nothing to do with either.

There's a second, independent reason the seam holds: correctness here depends on strict per-document ordering, which is far easier to reason about in a small, purpose-built service than as one code path buried inside a much larger system serving many unrelated concerns. If your product only ever needs single-editor documents with no real-time collaboration, this entire service doesn't need to exist yet — say so rather than building live-collaboration infrastructure nobody's using.

## Building blocks

| Block | Role |
|---|---|
| **WebSocket Gateway** | Terminates client connections; routes each document's traffic to whichever Document Session Service instance currently owns that document (cross-ref [Long Polling, WebSockets & SSE](../../scalability-resilience/long-polling-websockets-sse.md)'s point that a stateful connection has to reach a specific instance, not any instance) |
| **Document Session Service** (stateful, sharded by `document_id`) | Holds the live, in-memory document state for its assigned documents; runs the OT transform on every incoming operation; broadcasts the transformed result to every connected editor |
| **Operation Log Store** | Append-only, strictly ordered per document — the durability mechanism and the single source of truth for "what happened, in what order" |
| **Snapshot Store** | Periodic full-document snapshots, so replay after a restart or a reconnect never has to start from character one |
| **Presence Broadcaster** | Cursor positions and "who's viewing" — rides the same connection, deliberately excluded from the operation log (see Module 00's Q&A) |

## Per-path walkthrough

**Edit path (write)** — `Client → WebSocket Gateway (routes by document_id) → Document Session Service (transform against any ops since base_seq) → Operation Log (append, same step) → broadcast transformed op to every connected client for this document`. The transform and the log append happen together, in the order the session service processes operations for that document — see Concurrency at the code level in Module 02 for why this needs no separate lock.

**Reconnect / catch-up path (read)** — `Client reconnects with last-known seq → GET /documents/{id}/snapshot?since_seq= → Snapshot Store (latest snapshot at-or-before that seq) → Operation Log (replay everything after the snapshot, up to current) → client applies the delta and resumes`. Notice this is the *same* mechanism a brand-new viewer opening the document for the first time uses (with `since_seq=0`) — there's no separate "initial load" code path.

**Snapshot path (async, off the critical path)** — `Document Session Service (periodically, e.g. every N operations or every T seconds) → Snapshot Store (write current full state + seq number)`. Entirely decoupled from the edit path's own latency — a slow or temporarily-failed snapshot write never blocks or delays a single keystroke reaching other editors.

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Conflict resolution | Operational Transformation (server-sequenced) | CRDTs (peer-to-peer mergeable) | OT needs a central sequencer but keeps the document a plain, compact string; CRDTs skip the sequencer at the cost of a heavier per-character data structure that often never fully compacts back down |
| Routing a document's operations | Sticky routing to one Document Session Service instance per `document_id` | Any instance can handle any operation, coordinated via a shared lock per operation | Per-operation coordination would mean every keystroke pays a distributed-lock round trip; pinning ownership means the transform runs against purely local, in-memory state |
| Delivering an edit to other viewers | WebSocket (bidirectional, server-push) | Long polling | An edit has to reach every other viewer with no client-initiated request in between; cross-ref [Long Polling, WebSockets & SSE](../../scalability-resilience/long-polling-websockets-sse.md)'s framing of WebSockets as the right fit for genuinely bidirectional, high-frequency traffic |
| Recovering document state | Log + periodic snapshot | Log only, replayed from the beginning every time | An old, heavily-edited document's full history could mean replaying millions of operations on every reconnect; a snapshot bounds replay to "since the last snapshot," not "since the document was created" |
| Cursor position | Broadcast live, never logged | Written to the operation log alongside real edits | A stale cursor position is invisible and harmless if lost; treating it as log-worthy would inflate the log with high-frequency, low-value writes for no correctness benefit |

## Load Handling

- **Peak-vs-average tolerance:** the vast majority of documents have exactly one editor at a time; the design's real stress case is a small number of "hot" documents (a shared meeting-notes doc during a live meeting) with a few dozen concurrent editors, not an even load spread — this is a per-document hotspot problem, not a global-throughput one.
- **Where backpressure kicks in first:** at the Document Session Service instance owning a specific hot document. Because ordering has to be strict per document, that document's operations cannot be parallelized across more instances the way a stateless read path could scale — the mitigation is capping how many editors a single document can have live at once (a product-level limit) rather than pretending the transform itself can be sharded further.
- **What gets shed under overload:** presence/cursor updates are the first thing throttled or dropped under pressure — they're explicitly not correctness-bearing (see Trade-offs above). Real edit operations are never shed; if a session instance is genuinely overwhelmed, it's a capacity-planning failure for that shard, not something to solve by dropping keystrokes.
- **Autoscaling lag:** the WebSocket Gateway and stateless read paths (snapshot fetches) scale in the usual 1–3 minute horizon. The Document Session Service tier scales by adding shards for *new* documents, not by relieving an already-hot existing document — an already-overloaded shard needs its hot documents identified and, if truly necessary, manually rebalanced, not an automatic scale-out that can't help a single-document hotspot anyway.
- **Load-test target:** sustain 50 concurrent editors on one document, each typing continuously, with every operation transformed and broadcast to all 50 clients within the 200ms target, zero dropped or misordered edits.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| Two edits arrive at the session service having raced each other (an insert and a delete near the same position) | The later-processed operation is transformed against every operation that landed between its `base_seq` and the log's current head, before being applied — never applied raw | Nothing is "lost" for either side — there's no loser; both operations end up applied, with the later one's position adjusted so it lands where the user actually intended given what changed underneath it |
| A client's operation references a `base_seq` that's already far behind the log's current head (a slow or lagging connection) | The transform engine walks forward through every intervening operation in the log, transforming the incoming one against each in sequence, rather than assuming `base_seq` is recent | The client's edit still applies correctly, just against a "wider" transform chain — the mechanism doesn't degrade, only the amount of transform work per operation grows with lag |
| A snapshot write starts while new operations are still arriving for the same document | The snapshot captures the state *as of a specific sequence number*, and the log continues appending past it independently — the snapshot writer never blocks or is blocked by the ongoing edit stream | No client-visible effect at all — snapshotting is a read of a point-in-time state, not a lock on the document |

## Scaling & Reliability

- **Horizontal scaling:** adding more Document Session Service shards accommodates more *documents*, each new document assigned to whichever shard has capacity — the same sharding-by-key reasoning [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) applies generally, with `document_id` as the key that matches this system's actual access pattern (every operation for one document, together).
- **Circuit breaker:** the Snapshot Store write path is wrapped in a circuit breaker (cross-ref [Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md)) — if snapshotting is failing, the session service keeps serving live edits from in-memory state and the operation log regardless; a failing snapshot is a durability-recovery-time concern, never a live-editing outage.
- **Retries:** a failed snapshot write retries with backoff against the *same* target sequence number, never re-computing a new one mid-retry — the retry is idempotent by construction, since "snapshot as of seq N" is either written or it isn't.
- **Dead-letter queue:** not really applicable to the live edit path itself (an operation that can't be transformed is a bug, not a retryable failure) — but a snapshot write that exhausts its retries logs an alert rather than blocking, since the log itself remains the durable source of truth even with no recent snapshot.
- **Graceful degradation:** if the Snapshot Store is fully unavailable, live editing continues uninterrupted — only a *reconnecting* client is affected, and even then, it degrades to "replay from the last snapshot that does exist" (older, but still correct) rather than failing the reconnect outright.
- **Multi-region:** not built here — named as a real gap below.

## What you'd revisit as this grows

- **Multi-region collaboration.** This design assumes one Document Session Service instance, in one region, owns a given document's live state — a globally-distributed team editing the same document pays that instance's regional latency no matter where they sit. A genuinely global design needs either regional replicas with their own conflict-resolution layer on top of OT, or a CRDT-based rework, since OT's central-sequencer requirement doesn't shard across regions cleanly.
- **Rich formatting and structured content.** This module treats a document as a flat character sequence; real documents have bold, tables, comments, and embedded objects — transforming a plain-text edit against a structural one (deleting a table a comment is anchored to) is a substantially harder version of the same problem, deliberately out of scope here.
- **Permissions interacting with live edits.** What happens to an in-flight WebSocket session when an editor's access is revoked mid-edit is a real product question this module doesn't address — worth naming rather than assuming it falls out of the architecture above for free.
- **Log compaction at very old, heavily-edited documents.** Snapshotting bounds replay cost going forward, but a document with years of edit history still accumulates an ever-growing log behind each snapshot; a mature system needs an explicit retention/archival policy for operations well before the oldest live snapshot.
