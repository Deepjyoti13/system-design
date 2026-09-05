# Module 03 — Database Design

**Diagram for this module:** [URL Shortener — schema / ER diagram](https://claude.ai/code/artifact/bc5f3561-f62e-43b0-8ce1-0e19cf1bed1a)

## What DB design is actually for

HLD said "there's a database." LLD said "there's a `UrlRepository` interface." Neither one commits to what the data actually looks like on disk, what's indexed, or which trade-offs get made when the tables meet real traffic. That's this module.

## From entities to schema

Three entities fall directly out of the requirements in module 01:

- **`users`** — who owns a link (optional; anonymous shortening is allowed).
- **`urls`** — the mapping the entire system exists to serve.
- **`click_events`** — one row per redirect, for analytics.

Open the [ER diagram](https://claude.ai/code/artifact/bc5f3561-f62e-43b0-8ce1-0e19cf1bed1a) for the full field list. A few decisions are worth walking through rather than just reading off the diagram:

### Why `short_code` gets a unique index, non-negotiably

Every single redirect — the highest-traffic query in the whole system — is `SELECT long_url FROM urls WHERE short_code = ?`. Without an index, that's a full table scan against a table with, per the module 01 math, on the order of hundreds of billions of rows over a few years. The unique index isn't just an optimization here; it's also what makes the collision check in module 02 ("does this code already exist?") an O(log n) lookup instead of a correctness bug waiting to happen.

### Why `click_count` is denormalized

The "correct," fully-normalized way to answer "how many times has this link been clicked?" is `SELECT COUNT(*) FROM click_events WHERE url_id = ?`. That query is fine at low volume and gets slower as `click_events` grows — exactly the table growing fastest in the whole system, since it gets a row on every redirect. The fix is to keep a running `click_count` column directly on `urls`, updated asynchronously (by the analytics worker from module 01, off the critical path) rather than computed live. This is a genuine trade-off: you gain a cheap read, and you accept that `click_count` can lag the true count by however long the async update takes. Naming that lag explicitly — rather than letting it be an unstated surprise — is the actual skill here.

### Why `owner_id` is nullable, and indexed anyway

Anonymous link creation is a functional requirement, so `owner_id` has to allow null. It's still indexed, because "show me all the links I've created" is a real query pattern (any user-facing dashboard needs it) even though it's much rarer than a redirect.

### Why `click_events` gets a composite index on `(url_id, occurred_at)`

Analytics queries are almost never "all clicks ever" — they're "clicks on this link in this date range." A composite index with `url_id` first lets the database narrow to one link's rows before it even considers the timestamp, which is what makes date-range queries on a specific link fast without needing a separate index per column.

## SQL vs. NoSQL, for this system specifically

This design uses a relational database, and it's worth being explicit about why, rather than treating it as a default:

- The core query is a single-key lookup (`short_code → long_url`) — something a key-value store (DynamoDB, a Redis-backed store) would also serve extremely well, arguably with less operational overhead than a relational cluster.
- What relational buys here is the **join** between `users` and `urls` for "list my links," and straightforward **secondary indexes** for the analytics queries above — both of which are more awkward in a pure key-value model.
- If this system *only* ever needed the redirect lookup and never needed per-user dashboards or analytics, a key-value store alone would be a legitimate, simpler choice. The requirements — not a default preference for SQL — are what justify the relational schema here. This is the kind of call worth stating out loud in any design review: "I chose X because of requirement Y," not "I chose X because it's what I know."

## Scaling the schema

- **Sharding `urls`:** once a single primary can't hold write throughput or the whole table no longer fits comfortably in memory/cache, shard by a hash of `short_code`. Because every read is already a point lookup by `short_code`, the application always knows which shard to query — no cross-shard fan-out needed for the hot path.
- **Splitting `click_events` out entirely:** this table has the highest write volume and the least need for strong consistency or joins. At real scale it's a strong candidate to move off the primary relational cluster entirely, into a time-series or columnar store (ClickHouse, a managed time-series database) built for exactly this write pattern — leaving `urls` and `users` on a smaller, easier-to-manage relational cluster.
- **Read replicas vs. sharding:** module 01's read replicas solve *read throughput*. Sharding solves *write throughput and total data size*. It's easy to reach for sharding when a replica would actually solve the problem — worth asking "is this a read problem or a write/size problem?" before picking either.

## Connecting it back

Look at all three diagrams side by side now: the cache in the HLD diagram exists because of the read/write ratio in module 01's requirements; the `CacheClient` interface in the LLD diagram exists so that cache could later be swapped or removed without touching the service; and the unique index in this module is what makes both of those decisions actually work under load instead of just working on paper. That chain — requirement → architecture decision → interface → schema — is the thing to practice reproducing on a new problem, which is exactly what module 04 asks you to do.
