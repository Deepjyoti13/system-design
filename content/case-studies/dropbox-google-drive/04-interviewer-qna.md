# Module 04 — Interviewer Q&A

**1. What happens when two requests hit the same resource at the same instant?**
Two devices committing a new version of the same file at the same time resolve at the manifest compare-and-swap: `commitVersion` only succeeds if `based_on_version_id` still matches the file's current version, so the second committer's write is rejected and its client creates a conflicted copy rather than silently overwriting the first.

**2. What happens when traffic spikes 10x for an hour?**
This system's real spike shape is a reconnect burst (thousands of devices waking up after being offline), not steady edit traffic — the sync/notification service pages through bounded batches of changes per device rather than serving one unbounded response, so the burst is absorbed as slightly higher latency per device, not a service overload.

**3. Why chunk files instead of just diffing byte-for-byte on the server?**
Byte-for-byte diffing needs both the old and new full file present in one place to compare — exactly the transfer this design exists to avoid. Chunking pushes the diff decision to the client, which already holds both versions locally, so the server never needs to see more than the blocks that actually changed.

**4. How does this system avoid storing the same file twice when two different users upload an identical file?**
Content-hash addressing on `blocks` means identical byte content always produces identical hashes, so the second user's upload finds every block already present and uploads nothing — deduplication falls out of the storage key design rather than needing a separate "check if this file already exists" feature.

**5. Why doesn't this design use Operational Transform, like the Google Docs case study, to merge concurrent edits automatically?**
OT needs structural knowledge of the content being edited (characters, paragraphs) to compute a merge; this system is deliberately content-agnostic — it stores whatever bytes a file contains, video or binary included, with no way to interpret two versions well enough to merge them. Surfacing a conflicted copy is the honest alternative when automatic merging isn't possible (cross-ref [Google Docs' architecture](../google-docs-collab-editing/01-architecture-hld.md) for the case where merging *is* possible).

**6. How would you support very large files, like a 500GB video archive?**
The block size and chunking approach don't change — a 500GB file is simply ~125,000 blocks in its manifest instead of a few. The one thing that does need attention is that presigned uploads (Module 01) already parallelize across many blocks independently, so a very large file's initial upload is bounded by aggregate bandwidth across concurrent block transfers, not by one giant sequential stream.

**7. How would you delete a file and actually reclaim its storage?**
Deleting a file decrements the `ref_count` on every block its current manifest references (Module 03); a block whose `ref_count` reaches zero becomes eligible for a separate garbage-collection sweep to reclaim its storage — deletion itself is just a metadata operation, and the expensive reclaim work happens asynchronously, never on the delete request's own critical path.

**8. What happens if a device is offline for months and comes back with a huge backlog of changes?**
The `/changes?since_cursor` endpoint pages through the version history in bounded batches (Module 01's Load Handling); the device simply takes longer to catch up, processing pages sequentially, rather than the sync service attempting to compute and return an enormous single diff.

**9. How would you handle a user sharing a folder with 10,000 other users?**
The `shares` table's eventual-consistency tolerance (Module 03) is exactly what lets this fan out without a synchronous multi-write transaction — each grant is an independent row write, and each target user's own sync/notification feed picks up the new shared item on its own schedule, rather than the share operation blocking until all 10,000 propagate.

**10. Could you use this same design for something like a photo backup app?**
Yes, with the emphasis shifted: photo files rarely change after being taken (no chunked-diff re-upload story is needed for most of them), so block-level chunking mainly buys cross-device and cross-user dedup of identical photos (screenshots, forwarded images) rather than incremental-edit efficiency — the same content-addressable block store, used for a different dominant workload.
