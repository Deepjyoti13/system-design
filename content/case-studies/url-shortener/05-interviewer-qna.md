# Module 05 — Interviewer Q&A

Ten follow-ups this design actually invites, each answered from a decision already made rather than from a textbook.

---

### 1. You hash the URL to get the code. How do you handle collisions?

The premise is wrong, and correcting it is the answer. **This design doesn't hash.** [Module 02](./02-short-code-generation.md) rejects hash-and-truncate in favour of a block-allocated counter put through a bijective scramble, which makes collisions **structurally impossible** rather than merely rare.

The reason matters more than the choice. If you *do* hash, the collision rate is the load factor `n/N`. With a 7-character base62 code (N = 3.52 trillion) and 182 billion links after 5 years, that's **5.2% of inserts colliding — about 1 in 19, or 61 per second.** That's not an edge case to catch and log; it's a routine, high-volume code path whose frequency silently triples over three years. Handling it means salting and rehashing (never re-hashing the same input, which is deterministic and would collide forever), bounded to about 5 attempts, with a `UNIQUE` index as the actual arbiter because a `SELECT`-then-`INSERT` check is a check-then-act race.

Doing it with a counter instead: each instance atomically claims a range of 100,000 integers via CAS, hands them out from memory, and scrambles each one with `(counter × K + OFFSET) mod 62⁷` where `gcd(K, 62⁷) = 1`. Because that's a bijection, distinct counters always yield distinct codes. No retry loop, no probability, and create latency stays flat instead of growing a tail as the table fills.

I'd keep the `UNIQUE(code)` index anyway — but its job changes completely. It's no longer routine collision resolution; it's a tripwire for allocator bugs (a coordinator failover double-issuing a block, a restored snapshot rewinding the counter). And because it should now *never* fire, an alert on any unique violation is finally meaningful, which it could never be under hashing.

---

### 2. Sequential counters make links guessable. Someone can enumerate your entire corpus.

Correct, and it's the reason the scramble exists rather than plain `base62(counter)`. That would make `aZ3kQ1x` and `aZ3kQ1y` adjacent links, so anyone holding one short link could walk the whole database — every unlisted invite, private document and internal dashboard anyone ever shortened. This has happened to real shorteners.

`(counter × K + OFFSET) mod N` fixes it while keeping the zero-collision property: consecutive counters land `K` apart in a 3.5-trillion space, so neighbours are uncorrelated in the output.

The honest limitation: **modular multiplication is linear**, so an attacker who obtains several code/counter pairs can solve for `K` and then enumerate freely. If enumeration resistance is a genuine security requirement rather than a privacy nicety, swap in a small **Feistel network** over 42 bits — four rounds with any keyed hash as the round function. Still a guaranteed bijection (so uniqueness survives), but not linearly invertible.

Worth being precise about scope, though: this only protects against *enumeration*. It does nothing about someone who has a specific link, so genuinely private content still needs authentication, not an unguessable URL. An unguessable code is a speed bump, not access control.

---

### 3. Why 302 and not 301? A permanent redirect would let browsers skip your service entirely.

That's true, and it's exactly why it's wrong here. Two reasons, both traceable to stated requirements:

**It kills analytics.** Click tracking is a functional requirement. A cached 301 means subsequent clicks never reach the service and are never counted. You'd be trading the product's measurement story for infrastructure savings the cache tier already provides — the 95% hit rate makes the extra round trip cheap, so there's nothing to buy.

**It makes links unrevocable.** Browsers cache permanent redirects aggressively and with no reliable invalidation. This system has an abuse-blocklist requirement and an expiry feature; a client holding a cached 301 keeps hitting the old destination indefinitely after a takedown. Permanently caching a destination in clients you cannot reach is a liability.

The scenario where 301 *is* right: a pure vanity-domain redirect with no analytics, no expiry and no takedown risk — say `go.company.com` → the marketing site. Different requirements, different answer.

---

### 4. At 116k reads/sec, what happens when Redis goes down?

Reads fall through to replicas, which were sized for ~5,800 QPS on the assumption of a 95% hit rate. 116k against them is roughly 20× their design point, so this is a genuine degradation, not a graceful one, and I'd rather say that than pretend otherwise.

Three things make it survivable:

1. **The in-process LRU on the read tier** (`TieredCacheClient` from [Module 03](./03-lld.md#interfaces-vs-implementations)) keeps serving the hot set with no external dependency at all. Because link popularity is severely Zipfian, even a small per-instance cache covers a large share of traffic — this is the main thing standing between a Redis outage and an outage.
2. **Load shedding in the defined order** from [Module 01](./01-architecture-hld.md#load-handling): analytics events drop first, then create requests get `429`/`503`, and redirects are shed last. That ordering is the cash-out of the deliberately asymmetric availability targets (99.99% reads, 99.9% writes) — under pressure the system stops accepting new links to keep resolving existing ones.
3. **Cache-aside means Redis is not in the write path**, so no data is lost and no repair is needed. It's a cold cache that refills on demand, which is also why the recovery has a thundering-herd problem worth pre-empting with jittered TTLs and single-flight.

Immutability is what makes all of this easy: rows never change after insert, so a stale or empty cache can never be *wrong*, only slow.

---

### 5. Should the same long URL always return the same short code?

No, and this is a product decision that a schema keyword can accidentally reverse, so it's worth being explicit.

Deduplication is free under a hashing scheme, which makes it tempting, but it collapses four independent lifecycles onto one row:

- **Analytics** — two marketing teams shortening the same landing page would share one click count with no attribution.
- **Expiry** — if A sets `expires_at` and B doesn't, whose wins?
- **Deletion** — A deleting their link breaks B's.
- **Abuse** — revoking one code revokes it for every user who ever shortened that URL.

And under any non-hash strategy it costs a read-before-write anyway, so it isn't even free.

So: **no dedup**, and `long_url_hash` in [Module 04](./04-db-design.md#why-long-url-hash-exists-and-why-it-isnt-unique) is deliberately **non-unique**. It exists for abuse response — "find every code pointing at this URL" as an indexed lookup rather than a scan of 182 billion rows — and it optionally enables `?reuse_existing=true` as explicit opt-in ergonomics without imposing dedup semantics on everyone.

---

### 6. How do you stop someone scanning random codes to find private links?

This is the design's most realistic overload cause, and the important mechanical detail is that **a cache-aside cache holds only things that exist**, so every scan for a nonexistent code goes straight through to the database. A scanner at 50k requests/sec delivers 50k QPS of index lookups to replicas sized for 5,800. Rate limiting alone doesn't fix it, because a distributed scanner has as many IPs as it wants.

Four layers, cheapest first:

1. **Shape validation** — reject anything that isn't 7 base62 characters before any I/O.
2. **A Bloom filter of issued codes** at the read tier ([Bloom Filters](../../scalability-resilience/bloom-filters.md)). "Definitely absent" is answered locally with no network call. False positives fall through to the normal path, so it can only save work, never break correctness. This is where a Bloom filter genuinely earns its place in this design — under a hashing scheme it would have been a write-path optimization instead.
3. **Negative caching** — cache the 404 itself for ~60s. Bounded memory, and safe because a code can only start existing via a create, which invalidates the negative entry it contradicts.
4. **Per-IP rate limiting** ([Rate Limiting](../../hld-building-blocks/rate-limiting.md)) — real but insufficient alone, which is why layers 1–3 do the structural work.

And the framing worth adding: an unguessable code is not an authorization mechanism. Genuinely private links need auth. Scan defence protects *your infrastructure*; it doesn't protect the user's content.

---

### 7. Why shard on `hash(code)`? Why not by creation time, so old data goes cold?

Time-based sharding is the classic mistake for this workload. All 1,160 writes/sec would land on the newest shard while every historical shard sits idle — a total write hotspot, not a marginal one. Time sharding is right for append-mostly time-series data queried by range; this table is point-lookup-by-key with a 5-year read tail, which is the opposite.

`hash(code)` is chosen because **the shard key is derivable from the only input the hot path has.** `GET /{code}` computes the shard from the code itself: single-shard read, no scatter-gather, no cross-shard transaction, on 99.9% of queries. Nothing else has that property — `owner_id` is `NULL` for anonymous links and unknown at redirect time (so every redirect would fan out to every shard), and range-sharding on `code` would let lexicographically adjacent viral codes concentrate on one shard with no way to split them.

The price is that `INDEX (owner_id, created_at)` becomes local per shard, so the owner dashboard is a scatter-gather with awkward pagination. Acceptable — it's low-volume and latency-tolerant — and the named fix is a `links_by_owner` table sharded on `owner_id`.

Also worth separating two things that get conflated: **sharding solves capacity** (70 TB doesn't fit one node), **replication solves read throughput**. This design needs both, for those two independent reasons.

---

### 8. Your counter store is a single point of failure. What happens when it dies?

It is, and I'd rather name it than defend it. But the blast radius is much smaller than it looks, and the reason is a nice accident of the design.

Each instance holds a **pre-allocated block of 100,000 codes in memory**. When the counter store dies, creates keep working from those blocks — tens of minutes of runway at fleet scale — and only fail once blocks exhaust. Redirects are entirely unaffected, since they never touch the counter. So block allocation, chosen for throughput, doubles as a substantial availability buffer.

Beyond that: it's one row touched once per 100,000 links, which is roughly one write per 30 seconds fleet-wide. That's cheap enough to put behind a consensus-backed store (etcd, ZooKeeper — cross-ref [Replication & Consensus](../../hld-building-blocks/replication-consensus.md)) and get automatic leader election and failover, rather than a single database row.

The failure I'd actually worry about isn't unavailability, it's a **correctness** failure: a failover that double-issues a block, or a restored snapshot that rewinds `next_value`. Both produce duplicate codes pointing at different URLs — the silent-wrong-destination outcome this system can't tolerate. That's precisely why `UNIQUE(code)` is retained despite being theoretically unreachable, why `code_block_audit` records every issued range, and why an unexpected unique violation pages someone instead of being retried.

"We have tens of minutes to fix it manually" is a weaker answer at 3am than it sounds, and automatic failover here is real remaining work.

---

### 9. How do you support custom aliases without breaking the generated-code scheme?

Custom aliases are a different problem from generated codes: they collide **on purpose**, at whatever rate an adversary picks. Three concrete threats, and the third is a real vulnerability:

- **Land-grabbing** every dictionary word and brand name → per-account rate limiting plus reserved trademark strings.
- **Homograph abuse** — `paypaI` with a capital I → normalize confusables *before* the uniqueness check, or the unique index cheerfully accepts both.
- **Route shadowing** — an alias of `api`, `admin`, `login`, `static` or `health` shadows your own routes, because `GET /{code}` is a catch-all at the path root. A reserved-word denylist checked before insert is mandatory.

The structural answer, and the part I'd lead with: **keep the two namespaces provably disjoint.** Generated codes are always exactly 7 characters from `[0-9a-zA-Z]`, so require custom aliases to be **either ≥8 characters, or to contain a character outside that alphabet** (`-` and `_` are URL-safe and excluded from base62). With that single rule:

- A generated code can never land on a taken alias, so the counter needs no awareness of the alias table at all.
- An alias can never squat a code the generator will later issue, so there's no reserved-but-unissued bookkeeping.
- One `UNIQUE(code)` index still covers both, because disjointness is a property of the *values*, not the storage.

Without it, every generated code would need a check against the alias table before issue — reintroducing a read on the write path to defend against a collision the value scheme rules out for free. And it pays off again a layer down: [Module 04](./04-db-design.md#what-youd-revisit-as-this-grows) notes you can then split into a narrow `CHAR(7)` table and a `VARCHAR(64)` alias table without any risk of overlap.

---

### 10. Make this multi-region. What breaks?

Reads globalize almost for free, and for a specific reason: **rows are immutable once written.** Any replica in any region can serve any code with no read-your-writes problem, no conflict resolution, and no staleness that could ever be *wrong* — only cold. Put replicas everywhere, GeoDNS in front, done.

Writes are the hard part, and it splits cleanly into an easy half and a genuinely hard half.

**The easy half — generated codes.** Give each region its own counter namespace with a disjoint integer band (`us-east` from 0, `eu-west` from 10¹¹, and so on). Since the scramble is a bijection over the whole space, **disjoint input ranges guarantee disjoint output codes with zero cross-region coordination.** Each region mints independently and can never collide with another. `code_counter` is already keyed by `namespace` in [Module 04](./04-db-design.md#scaling-the-schema) for exactly this.

**The hard half — custom aliases.** Two regions can both accept `my-launch`, and there is no way to detect it without cross-region coordination *on the write path*, which is precisely what active-active is supposed to avoid. The options are all unattractive: a globally-serialized allocator for aliases only (a cross-region round trip on alias creation, so aliases become slow), per-region namespaces (`us.sho.rt/my-launch` — safe but a worse product), or optimistic acceptance with later conflict detection (which means telling a user their alias was revoked after they published it — unacceptable). I'd pick the globally-serialized allocator, accept ~150ms on custom-alias creation only, and keep generated codes fully local — but I want to be clear that's a compromise, not a solution.

The other unsolved piece: the abuse blocklist has to converge globally, and it currently fails closed on the create path. A regional partition therefore stops creates in that region rather than risking a spam relay — deliberate, and the right trade, but it means the blocklist's replication lag is directly a write-availability number, which this design hasn't quantified.
