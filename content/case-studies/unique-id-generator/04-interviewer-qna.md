# Module 04 — Interviewer Follow-Up Bank

1. **How would you assign a unique machine ID to each node automatically at startup?**
   Register with a coordination service (etcd/Zookeeper, cross-ref [Service Discovery](../../scalability-resilience/service-discovery.md)) that hands out the next free machine ID from the pool and holds a lease on it — if the process dies, the lease expires and the ID is freed rather than lost forever.

2. **Why put the timestamp in the highest bits instead of the lowest?**
   IDs are compared and sorted as plain integers. Putting the fastest-changing field in the highest bits means the integer ordering of the whole 64-bit value matches chronological order. If sequence bits were highest, IDs generated a millisecond apart could sort in the wrong order.

3. **What happens if you need more than 1,024 machines eventually?**
   The bit budget is fixed at design time — growing past it means shrinking another field (e.g. 8 bits machine / 14 bits sequence trades machine headroom for per-machine throughput) or moving to a wider ID entirely. Not something you patch after 64-bit IDs are already stored everywhere, which is why this trade-off is worth getting right up front.

4. **What happens when two requests hit the same resource at the same instant?**
   Two shapes of this race exist here, and neither needs a distributed lock: two threads in the *same* process calling `next()` concurrently resolve via a single atomic in-process update to `(last_timestamp, sequence)`; two *different* machines requesting a lease from the registrar at the same instant resolve via an atomic conditional write against the coordination store, so only one claims a given machine ID.

5. **What happens when traffic spikes 10x for an hour?**
   A near non-event for generation itself — per-machine headroom (4.096M IDs/sec) is orders of magnitude above what even a 10x spike demands (hundreds/sec/machine). The only thing that could actually stress the system is a mass simultaneous fleet restart during the spike's autoscale-up, which stresses the registrar's lease-granting, not ID generation.

6. **Why not just use a UUID and skip all this complexity?**
   A UUIDv4 is also coordination-free, but it's 128 bits of effectively random bytes with no time-ordering at all. Range scans and keyset pagination on a random primary key have far worse locality than on an ID that's roughly increasing — this design's requirement rules UUIDs out, not a blanket claim that they're worse everywhere.

7. **How would you test that two machines never actually produce colliding IDs, before trusting this in production?**
   Run N machines generating concurrently at sustained peak rate for a fixed window, dump every generated ID into a single set, and confirm the set's size equals the total count generated — any collision shows up as a smaller set. Pair this with the load-test target from Module 01 (1,024 simultaneous lease requests, zero duplicates).

8. **What happens to in-flight ID generation if a machine's lease unexpectedly expires while the process is still healthy?**
   Nothing changes locally — the process keeps generating under its cached `machine_id` regardless of what the registrar believes. The real risk is if the registrar reassigns that machine ID to a *new* process before the original notices; a lease timeout set generously relative to the heartbeat interval, plus fencing tokens (cross-ref [Distributed Locks](../../scalability-resilience/distributed-locks.md)), is what keeps that window narrow rather than pretending it can't happen.

9. **Could you make the sequence field larger at the cost of the machine-ID field, and when would that trade make sense?**
   Yes — fewer machine-ID bits caps how many machines can run concurrently, but more sequence bits raises the per-machine burst ceiling. Worth it if the real deployment has far fewer, far busier machines than the default 1,024/4,096 split assumes.

10. **How would you support IDs that also encode a shard key directly, so routing a write doesn't need a separate lookup?**
    Carve additional bits for an explicit shard field, or derive the shard as a modulo of the ID's low bits at read time — either way it costs bits that would otherwise go to the machine-ID or sequence fields, the same kind of trade-off named throughout this module.
