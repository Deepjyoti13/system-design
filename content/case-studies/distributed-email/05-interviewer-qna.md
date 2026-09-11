# Module 05 — Interviewer Q&A

---

### 1. How much storage do you need for a billion users?

The useful move is to notice the question has two very different answers, and the gap between them is the design.

```
1B users × 10 sent/day     = 10 billion emails/day
1B users × 40 received/day = 40 billion recipient COPIES/day     ← 4× amplification
```

The average message has four recipients. So:

| Approach | Bodies | Attachments | Total/year |
|---|---|---|---|
| Store the full email per recipient | 730 PB/yr | 1,460 PB/yr | **2,190 PB** |
| **Content-addressed, stored once** | 182 PB/yr | 365 PB/yr | **547 PB** |

**~1,640 PB/year saved** from one modelling decision: separate **the content** (identical for every recipient, immutable, ~50 KB) from **per-recipient state** (folder, read flag, labels — different per recipient, mutable, ~500 bytes).

And it pays off twice. The second payoff is subtler: **marking an email read touches 500 bytes instead of 50 KB**, and moving it between folders moves a pointer rather than a body. Since read-state changes are among the most frequent operations in the product, that matters as much as the storage number.

Dedup also extends beyond the recipient list — the same slide deck forwarded around a company is stored once regardless of how many separate emails carry it.

---

### 2. An email arrives over SMTP. Walk me through it.

```
1. Connection checks:  IP blocklisted? reverse DNS sane? rate-limited?   → 5xx, reject NOW
2. Envelope checks:    recipient exists? SPF pass? size OK?              → 5xx, reject NOW
3. Accept DATA; write DURABLY to the inbound queue
4. Reply 250 OK                          ← we now OWN this message
   ─────── everything below is async and retryable ───────
5. Virus scan; spam score; verify DKIM/DMARC
6. Store body + attachments content-addressed
7. Apply the user's filters → choose a folder
8. INSERT the per-recipient metadata row
9. Enqueue a search-index write
10. Push via WebSocket if the user is connected
```

**Step 4 is the most important line.** Under SMTP, `250 OK` is a **transfer of responsibility** — the sending server deletes its copy and considers the message delivered. Acknowledge and then lose it, and the email is gone with nobody holding a copy and nobody knowing. That's the "data loss is unacceptable" failure.

So the ordering is strict: **durably queue, then acknowledge.** Not the reverse (a crash loses mail silently), and not "process fully, then acknowledge" — because spam and virus scanning would hold the SMTP connection open for seconds, and a slow scanner causes the sender to time out and retry, duplicating work.

**Steps 1–2 reject before accepting the body**, deliberately. At spam volumes, transferring 50 KB before deciding means most of your inbound bandwidth is spent on mail you discard.

One thing I'd add unprompted: **duplicate delivery is routine, not exceptional.** SMTP retries after any ambiguous failure, so deduplicating on `(user_id, rfc_message_id)` is essential rather than defensive.

---

### 3. Why HTTP for your clients when email already has IMAP?

SMTP is non-negotiable — it's the federation layer, and without it you can't exchange mail with anyone.

For your *own* clients, HTTP wins on extensibility. **IMAP's command set is fixed and ancient**: it has no vocabulary for labels-instead-of-folders, snooze, conversation threading, server-side categorization, or push to a browser tab. Every one of those would need a protocol extension no client supports. A REST/JSON API works in a browser and pairs naturally with WebSocket for push.

The cost is real and worth stating: **you still have to implement IMAP and POP3** for users with existing mail clients. So you maintain three client protocols instead of one. That's a deliberate trade — pay for legacy support, don't let it constrain the product.

Worth knowing the distinction, since interviewers ask it as a set: POP3 **downloads and deletes** server-side (built for one device with local storage), IMAP **keeps mail server-side and syncs folder state** (built for multiple devices).

---

### 4. Design the schema for "show me my inbox".

The dominant query, so the schema is shaped around it:

```sql
CREATE TABLE messages_by_folder (
    user_id bigint, folder_id bigint, message_uuid timeuuid,
    from_email text, subject text, snippet text,     -- denormalized headers
    body_hash blob,                                   -- pointer to SHARED content
    is_read boolean, labels set<text>,                -- this recipient's own state
    PRIMARY KEY ((user_id, folder_id), message_uuid)
) WITH CLUSTERING ORDER BY (message_uuid DESC);
```

Three decisions:

**`(user_id, folder_id)` as the partition key.** `user_id` alone would make a two-million-message mailbox one enormous partition — and the partition is the unit of storage *and* of repair, so that's an operational problem before it's a storage one. Adding `folder_id` bounds it and matches the query exactly: one partition, one range scan.

**`message_uuid` is a TIMEUUID** — globally unique *and* time-sortable. So `CLUSTERING ORDER BY DESC` puts newest first physically, "most recent 50" stops immediately with no sort, minting one needs no coordination, and pagination is a cursor rather than an offset.

**Headers are denormalized into the row.** A folder listing shows sender, subject, snippet and read state — so it needs no second read, and critically it doesn't fetch 50 KB bodies. Fetching 50 full messages to render a list would be ~2.5 MB for a screen displaying a few kilobytes.

The accepted cost: **moving a message between folders is a delete plus an insert**, because folder is part of the partition key. That's fine because listing is far more frequent than moving, and the row is 500 bytes — the body doesn't move, only the pointer. Separating content from state pays off again, in a place you wouldn't expect.

---

### 5. How do you filter by unread?

With a separate table, because a wide-column store **cannot efficiently filter on a non-key column.** `WHERE is_read = false` means fetching the whole folder and filtering in memory — fine for 50 messages, unusable for a million.

```
Mail arrives     → INSERT into messages_by_folder AND unread_by_folder
Marked read      → UPDATE messages_by_folder;  DELETE from unread_by_folder
Marked unread    → re-INSERT into unread_by_folder
```

The trade is explicit: **duplicated storage plus a write-time consistency obligation, in exchange for a query that's otherwise impossible.**

And the honest risk: the two tables can **drift** on partial failure, leaving a row in `unread_by_folder` that the main table says is read. That presents as a **wrong unread badge** — confusing rather than harmful — which is why the unread count is periodically recomputed rather than trusted indefinitely.

The gap I'd flag: there's a repair but no *detection*. A systematic bug in the dual write would be invisible until users complained. A job that reports drift, not just corrects it, belongs in the design.

---

### 6. Email search — same as building a search engine?

Almost the opposite, on the two rows that matter.

| | Web search | Email search |
|---|---|---|
| Read:write | **Read-heavy** — index once, query billions | **Write-heavy** — 40B writes/day, a few queries/user/week |
| Ranking | By relevance (the hard problem) | **By date** |
| Completeness | Approximate is a feature | **Exact — missing a message is a bug** |
| Scope | The whole internet | **One mailbox** |

**It's write-heavy**, which is nearly unique among search systems, so the engine must be optimized for *ingest*. And **results must be exact** — web search returning the best 10 of a million is fine; email search failing to surface a message the user knows exists is a bug they remember.

The single highest-leverage fix, before choosing any engine: **keep mutable fields out of the index.** Index only immutable content (subject, body, from, to, date); keep `is_read`, `folder` and labels in the metadata store only. Under Lucene, documents are immutable, so updating a field means a tombstone plus a full reindex — **reindexing 50 KB to flip a boolean.** Removing mutable fields cuts index writes **4× (40B → 10B/day)** and eliminates that pathology entirely.

With that done I'd **start with Elasticsearch sharded by `user_id`** (so queries never fan out and merges stay local), and **move to a custom LSM index** only when merge pressure is measurably the binding constraint. At Gmail's scale that point arrives — which is why Gmail doesn't run Elasticsearch — but below it, building a multilingual search engine to avoid operating a cluster is a bad trade.

The general principle: **find and remove the write amplification before choosing the engine.** A 4× reduction helps whichever engine you pick, and it may make the simpler one sufficient.

---

### 7. Search must find mail that arrived seconds ago, but indexing is async. How?

A **hybrid query**: search the index for older matches, and simultaneously scan the user's most recent N messages directly from the metadata store, then union the results.

That works because mail access is heavily **recency-skewed** — recent messages are a tiny set and already cached, so scanning them is cheap. The index handles the bulk; a cheap exact pass covers what the index hasn't caught up with.

It's strictly better than tightening the index refresh interval, which would increase merge pressure — the exact thing that's already the binding constraint — to fix a problem affecting a narrow slice of queries.

Same **index-plus-recent-scan** shape that recurs across this guide whenever an asynchronous index has to serve a freshness requirement.

---

### 8. Why does mail from a brand-new server go to spam?

Because **the default posture toward an unknown sender is suspicion**, and that's the only posture that works — spammers cycle through fresh IPs constantly, so a receiving provider that trusted new IPs would be useless.

This is the part people don't expect: **deliverability isn't configured, it's accumulated.** Three mechanisms:

**Authentication — three DNS records that stack.** SPF lists authorized sending IPs, and **breaks on forwarding** (the forwarder's IP isn't in the record). DKIM signs the message cryptographically, so it **survives forwarding** — DKIM exists precisely because SPF's IP model couldn't. DMARC adds a **policy** (`none`/`quarantine`/`reject`) *and*, crucially, **alignment**: the visible `From:` domain must match the authenticated one. Without alignment a spammer passes SPF for `spammer.com` while displaying `From: paypal.com` — **alignment is what actually prevents spoofing**, and it's the part most explanations skip.

**IP warm-up.** Ramp from ~50/day to full volume over **2–6 weeks**, separately for each receiving provider. Which has a direct architectural consequence: **outbound capacity cannot autoscale.** In an otherwise fully elastic system, outbound send capacity is a weeks-ahead provisioning decision.

**Separate your mail classes onto separate IPs** — and this is the operational decision people get wrong most often. Reputation is per-IP, and marketing mail always attracts more complaints than transactional mail. Share the IPs and **a promotional campaign someone marks as spam makes your 2FA codes land in spam folders.** That's a catastrophic coupling between a low-stakes system and a critical one, caused entirely by an IP-assignment choice.

Plus: ingest **feedback loops** (the provider tells you when a user clicks "report spam" — without which a message filed in spam looks identical to a successful delivery), keep complaint rate under **~0.1%**, and honour hard bounces permanently. Repeatedly mailing nonexistent addresses is one of the strongest spam signals there is, because legitimate senders maintain their lists.

---

### 9. An email goes to a 50,000-member mailing list. What happens?

One inbound message becomes **50,000 metadata inserts, 50,000 index writes, and 50,000 push notifications** — a five-order-of-magnitude write amplification arriving as a single SMTP transaction.

Three things handle it:

**Content-addressing means the body is stored once.** 50,000 recipients cost 50,000 × 500-byte rows (~25 MB) instead of 50,000 × 50 KB (~2.5 GB). This is the third distinct place that one modelling decision pays off.

**Fan-out is asynchronous and batched**, which the inbound queue already provides — the SMTP transaction is acknowledged after step 4 and the 50,000 inserts happen behind it. Doing it synchronously would hold the connection open for the whole fan-out and cause the sender to time out and retry, duplicating a 50,000-way fan-out.

**Recipient count is capped** per message, which is also an anti-abuse control.

The related failure to be ready for: **a compromised account is a spam cannon.** The fastest way to destroy a mail system's reputation is one account sending a million messages before anyone notices — which is why per-account rate limiting sits on the *synchronous* send path where it can actually stop it, rather than in an async checker that notices afterwards.

---

### 10. What's the biggest weakness?

Three, and the first is structural rather than a gap.

**End-to-end encryption is incompatible with this design, not merely unbuilt.** Content-addressed dedup requires the server to see identical plaintext to compute identical hashes. Search indexing requires reading bodies. Threading requires reading headers. Spam and virus filtering require reading everything. So E2EE would simultaneously forfeit the 1,640 PB/year dedup saving, server-side search, threading, and spam filtering. That's a genuine architectural conflict, and a design that wants E2EE is a different design — not this one with a feature added.

**Shared mailboxes don't fit the partitioning scheme.** `support@company.com` accessed by twelve people is one mailbox with twelve readers, and partitioning by `user_id` assumes an email belongs to one user. The usual workaround — a pseudo-user with delegated access — breaks per-user read state, which is exactly what a shared inbox needs most. That's a completely ordinary business requirement, not an edge case.

**Content reference counting is unresolved.** Bodies and attachments can only be reclaimed when the last recipient deletes their copy. A refcount is correct but becomes a hot contended row for a widely-forwarded attachment; a mark-and-sweep GC is cheaper but must determine whether one hash is still referenced across millions of users' partitions — and neither the sweep interval nor that scan's cost is priced. The design leans on GC for the right reason (**orphaned bytes are cheap; a dangling pointer is data loss**) without specifying it.

Also worth naming briefly: **the `Message-Id` dedup window has no defined value** (too short duplicates a slow retry, too long grows the index unboundedly, and it depends on other providers' retry conventions that nobody controls), **unread counters drift** and their recompute cost for a million-message mailbox is unpriced, and **the design chooses consistency over availability at the storage layer** — a partition losing quorum stops serving rather than risking loss. That's correct given the requirements, and it's only affordable because **SMTP senders retry for days**, which is a constraint from 1982 quietly buying you something.
