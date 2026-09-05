# Design Dropbox / Google Drive

![Content-addressable block chunking: only the one changed block re-syncs, not the whole file](diagrams/hld.svg)

## Requirements

**Functional:** upload and download files and folders, sync changes across a user's multiple devices automatically, share a file or folder with another user. **Non-functional:** real numbers as assumptions — 500M users, ~10GB stored per user on average, sync latency under a few seconds for a small-file edit, and files ranging from tiny documents to multi-GB videos, all handled by the same system.

## Don't re-sync the whole file on every change

This guide's [object storage](../../scalability-resilience/object-blob-storage.md) page already covers multipart upload for large files — this design goes a step further. Every file is chunked into fixed-size blocks (say, 4MB), and each block is hashed; the block's ID **is** its content hash. When a file changes, only the blocks that actually changed need to be re-uploaded and re-synced — a 2GB video with one small edit re-uploads a handful of 4MB blocks, not 2GB.

This buys deduplication for free as a side effect, not a separate feature: if two versions of a file — or two different files entirely — happen to share an identical block, that block is stored once and referenced twice. Nobody had to write dedup logic; content-addressing already implies it.

## Syncing across a user's devices

Each client keeps a local index of which blocks make up which version of which file. On a change, the client diffs against its own last-known state, uploads only the new or changed blocks, and notifies other devices that a new version exists — either over a live channel ([long-lived connections](../../scalability-resilience/long-polling-websockets-sse.md)) or simple periodic polling for a less latency-sensitive design. A notified device then pulls down only the blocks it's missing, not the whole file.

The conflict case worth naming explicitly: two devices edit the same file while both are offline, then both reconnect with different local versions. This guide's [Google Docs](../google-docs-collab-editing/README.md) case study solves the equivalent problem with operational transformation — but that's the harder version of this problem, because Docs assumes many people can be editing the same document *at the same instant*. A file-sync client is normally edited by one device at a time; the offline-conflict case is rare enough that most real systems don't attempt automatic merging at all. They keep both versions as a "conflicted copy" and let the user resolve it by hand — a much cheaper answer to a much narrower problem.

## Architecture, briefly

- **Metadata service** — the file/folder tree, version history, and which blocks make up which version. Small structured records: a normal relational or document workload, nothing unusual here.
- **Block storage** — the actual chunk bytes, in [object storage](../../scalability-resilience/object-blob-storage.md), addressed by content hash.
- **Sync/notification service** — tracks which devices are online and pushes (or lets clients poll for) "a new version exists" signals, without itself moving any file bytes.

## Interviewer follow-ups

**How would you handle a user with 10,000 small files needing to sync at once, versus one 5GB file?**
These are different bottlenecks and the system should treat them differently: 10,000 small files is dominated by metadata-service request volume (batch the metadata operations rather than one round trip per file), while one 5GB file is dominated by block-upload bandwidth and benefits from parallel multipart-style block uploads — the same distinction this guide's object-storage page draws between a metadata call and the byte transfer itself.

**How does block-level deduplication interact with per-user storage quotas — does a shared block count against both users?**
Usually yes, logically: each user's quota is computed from the blocks *their* files reference, even if the underlying bytes are physically stored once. The alternative — not counting a shared block against either user — under-counts real usage and makes the quota meaningless; the storage saving is an implementation detail, not something that should leak into what a user is billed or limited for.

**How would sharing a folder with another user affect the metadata model?**
The folder's ownership and its access-grants need to be separate concepts: a share is a new entry linking a user to a file/folder with a permission level, not a change to who "owns" it. The block storage and content-hashing underneath don't change at all — sharing is purely a metadata-layer concern, which is exactly why keeping metadata and blocks in separate services pays off here.

**Would you version every single change, forever, or prune history?**
Keeping every version of every block forever is rarely worth it for typical documents — most systems keep recent versions at full granularity and coarsen older history (keep daily snapshots after a week, monthly after a few months), the same "match retention to how the data is actually used" instinct this guide applies elsewhere to logs and analytics data.
