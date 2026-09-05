# Design a Unique ID Generator

![Snowflake-style 64-bit ID: timestamp, machine ID, and per-ms sequence bit fields](diagrams/hld.svg)

## Requirements

Generate a unique ID for every new row across a distributed, **sharded** system — no two shards, no two machines, may ever hand out the same ID. Non-functional, stated as assumptions: ~100K IDs/sec across all shards combined; IDs should be roughly time-sortable (a newer ID is numerically larger, useful for range scans and pagination without a separate timestamp column); and — the hard constraint — no coordination between machines on the hot path. A generator that has to ask a central authority "is this ID free?" before handing it out has just reintroduced the single-node bottleneck this design exists to remove.

## Why a database auto-increment doesn't work here

An auto-increment column is, underneath, a single counter owned by one node. That's fine until the table is sharded (see [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md)) — the instant there are two independent database instances each running their own auto-increment, both can hand out `id=501` for two completely different rows, on two different shards, with neither one aware the other exists. The generator has to produce global uniqueness *without* a global sequence.

## The Snowflake-style approach

The practical answer real systems converge on: build the ID out of **concatenated bit-fields**, each contributing a different guarantee, so no two fields ever have to talk to each other to stay unique.

A common 64-bit layout:

- **41 bits — timestamp**, milliseconds since a custom epoch (not Unix epoch — a custom, more recent epoch buys more years before the field overflows). This is what makes IDs roughly sortable by creation time.
- **10 bits — machine/worker ID**, assigned once to each ID-generating process. Two machines can never produce the same ID *at the identical millisecond* because this field differs.
- **12 bits — sequence number**, a per-machine, per-millisecond counter that increments for every ID generated within that same millisecond on that same machine, and resets to zero at the next millisecond.

The bit widths aren't arbitrary — they're the actual limits of the design. 10 bits of machine ID caps this scheme at 2¹⁰ = 1,024 concurrently-registered machines. 12 bits of sequence caps a single machine at 2¹² = 4,096 IDs *per millisecond* (4.096M/sec per machine) before it has to stall and wait for the clock to tick over. Both numbers are worth saying out loud in an interview — they're the concrete ceiling this specific layout has, not a vague "it scales."

Two machines generating IDs in the exact same millisecond never collide: their timestamp bits are identical, but their machine-ID bits are guaranteed different, which is enough on its own to make the full 64-bit IDs different.

## The clock problem

The one real operational risk: **a machine's clock moving backward.** If an NTP correction or a VM migration rewinds a machine's clock after it already generated IDs at a later timestamp, it could — moving forward again from the earlier time — regenerate a timestamp+sequence combination it's already used, producing a duplicate or an ID that sorts *before* IDs it should come after. The practical mitigations: detect a clock regression at generation time and refuse to hand out new IDs until the clock catches back up to the last-seen timestamp (safe, but stalls that machine briefly), or treat it as a rare, monitored edge case with a documented recovery runbook rather than a hard guarantee. Neither option is "solve it in code and forget it" — this is a real operational surface, not a hypothetical.

## What this gives up

Strictly sequential, **gapless** IDs — the kind a legally-mandated invoice-numbering system needs, where "1, 2, 4" (skipping 3) is a compliance failure, not just an oddity. This design's sequence field resets every millisecond and per machine, so gaps and reordering across machines are normal, not bugs. Gapless numbering is a genuinely different problem, and it needs a genuinely different design: a single coordinated counter, accepting the throughput ceiling that comes with a single point of coordination. Don't reach for Snowflake-style IDs and then quietly assume they're also gapless — they're not, by construction.

## Interviewer follow-ups

**How would you assign a unique machine ID to each node automatically at startup?**
Register with a coordination service (e.g. Zookeeper/etcd, see [Service Discovery](../../scalability-resilience/service-discovery.md)) that hands out the next free machine ID from a small pool and holds a lease on it — if the machine dies, its ID is freed after the lease expires rather than being lost forever.

**Why put the timestamp in the highest bits instead of the lowest?**
Because IDs are compared and sorted as plain integers. Putting the fastest-changing field (timestamp) in the highest bits means the natural integer ordering of the whole 64-bit value matches chronological order. If the sequence bits were highest instead, IDs generated a millisecond apart could sort in the wrong order.

**What happens if you need more than 1,024 machines eventually?**
The bit budget is fixed at design time, so growing past it means either shrinking another field to make room (e.g. 8 bits machine ID / 14 bits sequence trades machine headroom for per-machine throughput) or moving to a wider ID (128-bit, like a ULID/UUID variant) — not something you patch after 64-bit IDs are already stored everywhere, which is why this trade-off is worth getting right up front.
