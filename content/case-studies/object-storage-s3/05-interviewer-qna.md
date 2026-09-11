# Module 05 — Interviewer Q&A

---

### 1. Walk me through how you get eleven nines of durability from drives that fail 0.81% of the time.

The first thing to get right is the *model*, because the obvious calculation is wrong. Most people say: 3 copies, so `0.0081³ = 5.3 × 10⁻⁷`, about 6 nines. That silently assumes **no repair ever happens** — it's asking "what's the chance all three drives die sometime this year," which would only matter if dead drives were never replaced.

Real systems detect failure in seconds and rebuild in hours, so data is lost only if the remaining copies fail **inside the repair window**. That makes the governing parameter mean time to repair:

```
P(loss) ≈ 6 λ³T²    for 3 copies, λ = 0.0081/yr, T = repair time in years
```

With a 1-day MTTR that's `2.4 × 10⁻¹¹` — **10.6 nines**, four orders of magnitude better than the naive answer. And because T is *squared*, halving repair time improves durability ~4×.

The three mechanisms, in order of importance:

1. **Erasure coding 8+4** — any 8 of 12 fragments reconstruct the object. At 1-day MTTR that's ~15.7 nines, and it stores 1.5× rather than 3×. It's better on *both* axes, which is rare.
2. **Failure-domain-aware placement** — all that arithmetic assumes independent failures, and they aren't. Twelve fragments in one rack means one PDU is your durability. Spreading across racks and datacenters is what makes the independence assumption approximately true.
3. **Continuous scrubbing** — detectable failures are the easy case; silent corruption is the hard one, and redundancy doesn't help if you can't tell which copy is wrong.

The thing I'd flag: **MTTR is a durability parameter, not an ops metric.** If a 20 TB drive takes 3 days to rebuild because the network can't go faster, you have 9.7 nines, not 11 — and no amount of redundancy accounting fixes it. Repair bandwidth is a durability budget line, which is why [Module 01](./01-architecture-hld.md#load-handling) gives repair a guaranteed I/O floor that user traffic can't preempt.

---

### 2. Erasure coding is cheaper and more durable. Why not use it for everything?

Because of amplification, and it comes in two forms with two different victims.

**Read amplification kills small objects.** Reading a 4 KB object under 8+4 means fetching eight 512-byte fragments from eight nodes — **8× the IOPS to move the same 4 KB.** [Module 00](./00-overview.md#capacity-estimation) established that a drive supplies only 100–150 random IOPS, so spending 8 of them on one small read is ruinous. Replication reads it with one seek.

**Repair amplification attacks durability itself**, and this is the more interesting one. Rebuilding a lost 10 MB fragment requires reading **8 fragments — 80 MB** — and computing over them. Replication rebuilds a lost 10 MB copy by reading 10 MB. So EC's repair costs 8× the bandwidth.

Now combine that with answer 1: repair speed determines durability. **EC makes repair 8× more expensive, which lengthens MTTR, which reduces durability.** The two effects fight. The comparison table in [Module 02](./02-durability.md#what-the-numbers-actually-say) assumed equal MTTR for both schemes, which flatters EC — in a bandwidth-constrained cluster EC's real advantage is smaller than the arithmetic suggests, and it shrinks as you approach the bandwidth limit.

So: a **hybrid split by object size** — 3× replication under 1 MB, EC 8+4 above it. What makes that obviously right rather than a hedge is that object count and byte count have completely different distributions: small objects are 20% of the *count* but under 1% of the *bytes*. So replicating them 3× costs a rounding error of capacity while buying a large IOPS win, and erasure-coding the large objects captures ~97% of the available savings.

---

### 3. Do you write metadata or data first? Why does it matter?

**Data first, always**, and it's the single most important ordering decision in the design.

Write bytes, confirm the durability quorum, *then* commit the metadata row that names them. A crash between the two leaves **orphaned bytes** — data on disk nothing points at, invisible to users, reclaimed later by the garbage collector. That costs disk space, which is cheap and recoverable.

Reverse it and a crash leaves a **dangling pointer** — a metadata row promising an object whose bytes were never durably written. The user got a `200`, sees the object in a listing, and gets a `500` or corrupt data on read. That's silent data loss reported as success, and it's unrecoverable.

Given the stated asymmetry — 11 nines of durability against only 4 nines of availability — trading reclaimable garbage for the elimination of dangling pointers isn't close. And the same principle recurs one layer down in [Module 03](./03-lld.md#pseudocode-write-path): the data node `fsync`s the packed file *before* recording the offset in its local index, so a crash there also leaves unreferenced bytes rather than an index entry pointing at nothing. **Garbage is cheap; dangling pointers are data loss** — that's the rule, applied at every layer.

---

### 4. Why not one file per object? That seems much simpler.

It is simpler, and it doesn't survive the IOPS arithmetic. Four separate costs, and they apply to different object sizes:

1. **Directory traversal on every read.** `open()` on a path walks the directory tree, and with 675M entries the dentry cache misses, so each level can cost a seek. One object read becomes *several* seeks before any data moves. Packing replaces that with one lookup in a local embedded index plus **exactly one** positioned read.
2. **File creation is a filesystem journal transaction** — allocate inode, write directory entry, journal both, `fsync`. Appending to an already-open file is far cheaper. At sustained write rates the file *creation* rate becomes the bottleneck, not the byte rate.
3. **Inode exhaustion.** `ext4` provisions inodes at format time, so you can hit the ceiling with free bytes remaining — and `fsck` time scales with inode count, turning a reboot into hours.
4. **Block rounding** — every file takes whole 4 KB blocks. This one is usually overstated: it's over 100% overhead for a 2 KB object and under 1% for a 500 KB one.

So objects are packed into 4 GB append-only files with a per-node `object_mapping` index recording `(file, offset, length, checksum)`.

But I'd scope the claim honestly: **this is a small-and-medium-object optimization.** A 200 MB object amortizes all four costs to nothing, so large objects get their own files — which also makes range reads, deletion and compaction simpler for them. Presenting packing as a universal rule is a mistake.

The real cost of packing is **write serialization**: one file has one tail, so concurrent appends contend on a lock. The fix is one open file per core, which trades a modest file-count increase for eliminating contention.

---

### 5. If files are append-only, how do you ever delete anything?

You don't delete; you mark and rewrite. A `DELETE` sets `deleted_at` in the data node's local index — you can't punch a hole in the middle of an append-only file.

Space comes back through **compaction**: scan a *sealed* file, copy the still-live objects into a new file, atomically repoint every index entry in one transaction, then unlink the source.

Three details make it correct:

- **Only sealed files are compacted.** The active file has a moving tail; compacting it would race with writers. Sealing at 4 GB gives an immutable input.
- **The index update commits before the source is unlinked.** A crash mid-compaction leaves the index pointing at the old file, which still exists — the half-written destination is itself orphaned garbage the next pass reclaims. Crash-safe, and again the failure mode is garbage rather than a dangling pointer.
- **A threshold gates it** (compact only below ~70% live). Rewriting a 4 GB file that's 69% live moves 2.8 GB to reclaim 1.2 GB, competing with user traffic *and* repair for the same disks. This is the classic LSM space-versus-write-amplification trade, and repair wins the scheduling conflict — repair protects durability, compaction only protects cost.

Garbage has more sources than deletes, incidentally: orphaned bytes from crashes (see answer 3), abandoned multipart parts, superseded versions, and fragments replaced after failing a checksum.

---

### 6. How do you handle a 5 TB upload?

Multipart upload, and the important part is what happens at the end.

`POST ?uploads` returns an `upload_id`. The client splits the file and `PUT`s parts **in parallel**, each independently retryable — so a failed part costs one part, not 5 TB. Each part is stored as its own object with its own UUID, and `PRIMARY KEY (upload_id, part_number)` makes a retried part upload naturally idempotent. `POST ?uploadId` completes it, with the client sending every part's etag so the server can verify nothing is missing or corrupted.

**Completion is a metadata stitch, not a byte copy.** The finished object's metadata references the *ordered list of part UUIDs*; the bytes are never rewritten. Concatenating 5 TB to make it contiguous would double the write cost and take hours, for a layout benefit that range reads don't need. That's exactly why [Module 04](./04-db-design.md#from-entities-to-schema)'s schema has an `object_parts` table rather than a single pointer.

Two consequences worth naming: an object's data is legitimately **non-contiguous**, so the read path must handle a part list. And the multipart ETag is deliberately *not* the whole-object MD5 — it's a hash of the concatenated part hashes plus a part count (`…-12`), because computing a real MD5 would mean reading all 5 TB back after assembly. That the ETag's meaning changes between upload types is a real API wart, and it exists because the cheap-to-compute answer beat the semantically clean one.

Abandoned uploads are swept after 7 days via `INDEX (initiated_at)`; without that, incomplete uploads leak storage forever.

---

### 7. `LIST` with a prefix — how does that work when you're sharded?

Badly, if you only have one table, and the reason is worth being precise about: **`LIST` fights the shard key.**

`objects` is sharded on `hash(bucket_id, object_key)`, which is perfect for `GET` — the shard is computable from the request, so every point lookup is single-shard. But hashing **destroys lexicographic locality by design**, so keys sharing a prefix scatter across every shard. A prefix `LIST` then means fan out to all shards, merge-sort in the API service, return the first 1,000. And pagination is worse than the fan-out: a continuation token can't be an offset, so "the next 1,000 after key X" re-queries every shard again. With 64 shards that's **64 queries per page**.

The fix is a denormalized read model: **`object_listing`, clustered on `(bucket_id, object_key)` and sharded on `bucket_id` alone.** All of one bucket's keys live on one shard in lexicographic order, so a prefix list is a single-shard range scan and pagination is just continuing the scan. It's CQRS applied to a schema — a write-optimized table plus a read-optimized projection, fed by CDC.

Three honest costs:

- **`LIST` becomes eventually consistent** — a just-uploaded object may not appear for a second or two, while `GET` on it works immediately. Real object stores behave exactly this way, and it's the right trade.
- **A hot shard per hot bucket**, since one bucket's listing rows are all co-located.
- **It makes listing a billion keys possible, not fast.** A million sequential pages is a million requests regardless of indexing. Listing is slow by construction; the schema only decides whether it's linear or quadratic. A bucket that large really wants a nightly inventory manifest instead of a live `LIST` — which is what real object stores offer.

---

### 8. S3 used to be eventually consistent and now isn't. What changes?

The old behaviour is visible directly in the schema. If metadata is replicated *eventually* across zones, a `GET` routed to a lagging replica returns the previous version of an overwritten object — or a `404` for one that was just created.

Strong read-after-write requires routing a key's reads to a replica guaranteed to hold its latest commit: either a quorum read, or a single primary per shard. This design picks **single primary per metadata shard, with `GET` served from the primary**, accepting the availability cost of that primary being a bottleneck and a failure point.

What makes that affordable is the metadata/data split. **Only the pointer needs strong consistency**; the bytes are immutable, so any replica or fragment set is equally valid and no consistency question arises about them at all. So the expensive guarantee is bought on a **0.7 TB** store rather than a **100 PB** one — three orders of magnitude cheaper. That's the payoff of the separation from [Module 00](./00-overview.md#approach-walkthrough) showing up somewhere non-obvious.

One thing that stays eventually consistent on purpose: `object_listing` (answer 7). So the system is strongly consistent for `GET` and eventually consistent for `LIST` — a deliberate split, not an oversight.

---

### 9. A whole datacenter goes down. What happens?

Depends entirely on the layout, and the common claim needs sharpening.

With 8+4 across three datacenters, four fragments each: losing one DC loses **exactly four fragments.** The scheme tolerates four. So data survives — and it survives with **zero remaining margin.** One additional drive failure anywhere in that stripe is unrecoverable data loss.

"Survives the loss of a datacenter" and "survives the loss of a datacenter with headroom" are different promises, and this layout only makes the first one. That's why repair becomes the highest-priority workload in the entire cluster the moment a DC drops — you're racing to restore margin, and every hour at zero margin is durability you're spending.

The alternative layouts are a genuine choice:

| Layout | Margin after 1 DC loss | Cross-DC write traffic |
|---|---|---|
| 8+4 over 3 DCs | **Zero** | 2/3 of every write |
| 8+4 over 4 DCs | 1 failure | 3/4 of every write |
| 12+4 over 4 DCs | Zero, at 33% overhead | 3/4 of every write |

Spreading wider buys margin and pays in cross-DC bandwidth **on every single write** — the most expensive network you own. I'd default to 4 DCs for anything with a real durability SLA, and I'd want the inter-DC transit price before committing.

Meanwhile: the placement service's Raft group (5–7 nodes) must itself be spread so that one DC can't take its quorum. If it loses quorum, the cluster map freezes — reads continue from cached maps, and **writes stop deliberately**, because placing new data without an authoritative map risks two halves independently assigning the same UUID. Failing writes beats guessing.

---

### 10. Redundancy doesn't help if you can't tell which copy is wrong. How do you handle silent corruption?

Right, and at this scale it's a routine event rather than an anomaly. Undetectable-error rates run around 10⁻¹⁵ per bit read; multiply by 100 PB (8 × 10¹⁷ bits) and corruption is *expected*, continuously.

The danger isn't just serving bad bytes — it's using a corrupt copy as the **source for repairing the others**, which propagates corruption into every replica and destroys the object permanently.

Three layers, all necessary:

1. **A whole-object checksum** (SHA-256) in metadata, verified before any byte returns to a client. Critically, the check lives **inside the data store** ([Module 01](./01-architecture-hld.md#per-path-walkthrough)), so a corrupt read becomes a reconstruct-and-repair rather than a corrupt response.
2. **Per-fragment checksums**, verified *before* reconstruction. Under EC this is non-negotiable: feeding one bad fragment into Reed–Solomon produces plausible-looking garbage with **no error raised**. Verifying only the reassembled object would leave you knowing something is wrong and unable to tell which fragment lied.
3. **Continuous background scrubbing** — each node re-reads its own data on a rolling schedule and re-verifies. The scrub period is itself a durability parameter: corruption found on a 30-day cycle has had 30 days to accumulate alongother failures.

The limitation I'd volunteer: **range reads can't be verified** against a fragment-level checksum, because there's no checksum covering just that slice. Since range reads are most of the real traffic (video streaming is built on them), that's a genuine hole. The fix is per-block checksums at a fixed granularity — every 64 KB — which costs index size and is the first practice exercise in [Module 03](./03-lld.md#practice-extend-it-yourself).

And scrubbing contends for exactly the disk I/O that user traffic and repair want — a three-way conflict that Module 01's guaranteed-floor policy exists to arbitrate.
