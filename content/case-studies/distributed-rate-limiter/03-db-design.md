# Module 03 — Database Design & Scaling

![Counter store data model: key structure, value shape, and sharding — not a relational schema](diagrams/er.svg)

There's no relational schema here — the "database" *is* the counter store, and its access pattern is pure high-throughput key-value: read state, compute, write state, no joins, no multi-row queries. That's exactly the query-pattern argument [SQL vs NoSQL](../../database-design/sql-vs-nosql.md) already makes for reaching past a relational store: a relational DB would add transaction overhead and query-planning cost this workload never uses.

**Key:** `ratelimit:{client_id}` — one key per client (not per client-per-window; the token-bucket state is continuous, not bucketed by wall-clock window, which is the entire point of using token bucket over fixed window).
**Value:** a Redis hash — `{ tokens: float, last_refill_ms: int, bucket_size: int, refill_rate: float }`. `bucket_size` and `refill_rate` are looked up from the client's tier config (free/paid) at first write and stored alongside so every subsequent check is a single key read, not a join against a separate config store.
**TTL:** set to a few multiples of the time it'd take an empty bucket to fully refill — long enough that an active client's state never expires mid-use, short enough that a client who stops calling entirely doesn't hold memory forever.

**Scaling:** as tracked-client count grows into the millions, [consistent hashing](../../hld-building-blocks/consistent-hashing.md) across more Redis shards is the same lever [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) describes generally — here specifically by `client_id`, since every access is single-key and there's no cross-client query that sharding would break.
