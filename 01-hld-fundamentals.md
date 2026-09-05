# Module 01 — High-Level Design (HLD)

**Diagram for this module:** [URL Shortener — architecture diagram](https://claude.ai/code/artifact/e3177f5c-0788-4be8-a830-f4de2a915547)

## What HLD is actually for

High-level design is the conversation you have *before* anything gets built: what are the major pieces, what does each one own, and how does a request travel between them? Get this wrong and no amount of good code in module 02 saves you — you'll have built the wrong boxes. HLD deliberately stays above the level of classes and tables; those come later, once the shape of the system is settled.

## Step 1: requirements, before any boxes

Every real design conversation starts here, and skipping it is the most common mistake — it's easy to jump straight to drawing a load balancer.

**Functional requirements** (what the system does):
- Given a long URL, return a short one.
- Given a short URL, redirect to the original long URL.
- *(Stretch, not required for the core design)* let a user pick a custom alias, or set an expiration date.

**Non-functional requirements** (how well it has to do it — these are what actually drive the architecture):
- **Scale:** assume 100M new short links created per day, and a 100:1 read-to-write ratio (redirects vastly outnumber creations — people click links far more often than they mint new ones).
- **Latency:** a redirect should resolve in well under 100ms — nobody tolerates a slow bounce.
- **Availability:** redirects should keep working even if the write path is degraded; a link that already exists is more valuable to keep serving than a new one is to keep accepting.
- **Uniqueness:** two different long URLs must never collide on the same short code.

Notice what falls out of just these four bullets: because reads dominate writes 100:1, *the read path is the one worth optimizing first* — that single fact is why the architecture below puts a cache in front of the database instead of, say, optimizing the write throughput.

## Step 2: the building blocks

These reappear in almost every HLD, not just this one:

- **Load balancer** — spreads incoming requests across many identical app servers, and stops sending traffic to one that fails a health check.
- **Application / API servers** — stateless request handlers. "Stateless" is the important word: any server can handle any request, which is what makes it possible to add more of them under load (horizontal scaling) instead of buying a bigger single machine (vertical scaling).
- **Cache** — a fast, usually in-memory store (Redis, Memcached) that sits in front of the database for data that's read far more than it's written.
- **Primary database + read replica(s)** — the primary accepts writes; replicas are copies kept in sync (with a small lag) that absorb read traffic so the primary isn't the bottleneck for both.
- **Message queue** — lets one part of the system hand off work to another without waiting for it, so a slow or non-critical task (like recording analytics) never blocks the critical path (serving the redirect).

## Step 3: the worked design

Open the [architecture diagram](https://claude.ai/code/artifact/e3177f5c-0788-4be8-a830-f4de2a915547) alongside this. There are three separate flows through the same set of boxes:

**Write path — creating a short link**
`Client → Load Balancer → App Server → Primary DB`
The app server validates the long URL, generates a short code (module 02 covers exactly how), and writes the mapping. This path is rare (module 01's whole premise) so it doesn't need to be blazing fast — it needs to be *correct*, above all never handing out the same short code twice.

**Read path — resolving a redirect**
`Client → Load Balancer → App Server → Cache` (check first) `→ Read Replica` (only on a miss) `→ back to Cache` (populate it) `→ Client`
This is the path that runs 100x more often than the write path, so it's the one that gets the cache. On a cache hit — the diagram estimates ~95% of requests — the database is never touched at all.

**Async path — click analytics**
`App Server → Queue → Analytics worker → separate store`
Counting a click is not something the user is waiting on, so it's decoupled entirely: the app server fires an event and immediately returns the redirect. If the analytics worker falls behind or even goes down for a few minutes, redirects keep working perfectly — that's the whole point of the queue.

## Back-of-envelope math (worth doing out loud, always)

- 100M writes/day ≈ 1,160 writes/sec average.
- 100:1 ratio → ≈ 116,000 reads/sec average — this is the number that has to survive, and it's why the cache exists.
- If an average short-URL row is ~100 bytes and you keep 5 years of links, storage is roughly 100M × 365 × 5 × 100 bytes ≈ 18TB — large, but well within what a sharded relational store or a managed key-value store handles routinely (module 03 picks this up).

## Trade-offs to make explicit

- **Cache-aside vs. write-through:** this design reads from the cache but only populates it lazily on a miss (cache-aside). A write-through cache — populating it at creation time too — would raise the hit rate slightly at the cost of extra work on every write, for a path that's already rare. Not worth it here.
- **Sync vs. async click counting:** counting synchronously (incrementing a counter on every redirect, in the request path) is simpler but adds latency and load exactly where you can least afford it. Async costs you eventual — not immediate — consistency on click counts, which is a fine trade for a number nobody is staring at in real time.
- **One region vs. multi-region:** this design assumes a single region. At real global scale you'd replicate the read path (cache + replica) into each region and keep writes centralized or use conflict-free ID generation — worth knowing this exists, not worth building for a "simple" version.

## What you'd revisit as this grows

- The primary DB becomes a bottleneck for writes long before it does for reads (that's what the cache is for) — the fix is sharding the `urls` table by short code, covered in module 03.
- A single message queue and one analytics worker are a single point of delay, not correctness, if they fall over — but at high enough volume you'd want to partition the queue too.
- Custom aliases and expiration (the stretch requirements above) both change the write path slightly — worth trying to slot them into this same diagram once you've read module 02.
