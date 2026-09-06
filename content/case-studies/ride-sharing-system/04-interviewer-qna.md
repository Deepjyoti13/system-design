# Module 04 — Interviewer Q&A

**1. What happens when two ride requests try to claim the same driver at the same instant?**
The atomic conditional update (`WHERE status='available'`) means only one claim's `UPDATE` affects a row; the other gets zero rows affected and is immediately re-matched to the next-nearest available driver from the same candidate set, not returned to the rider as an error.

**2. What happens when traffic spikes 10x for an hour — a concert or a stadium event ending?**
Ride-request volume at 10x is still small in absolute terms (~1,200/sec, trivial for the matching service), but it's *geographically concentrated*, which spikes the number of riders and drivers in the same handful of geohash cells. The real risk is contention on a small set of "hot" cells, not raw throughput — mitigated by finer-grained cells in known high-density zones and by the matching service backing off to a slightly wider cell radius if the immediate 9-cell neighborhood is momentarily saturated with claim conflicts.

**3. Why bucket by geohash instead of just running a radius query against a spatial index in the primary database?**
A database spatial index (R-tree/PostGIS-style) works, but at 1.25M writes/sec it would mean 1.25M index updates/sec against a durable store — the geohash-in-Redis approach keeps that write rate in memory, where it belongs, and only ever persists a *trip*, which is orders of magnitude rarer.

**4. How would you handle a driver's app losing connectivity mid-trip?**
The trip stays `in_progress` — the connection dropping isn't a state transition. The last known location simply stops updating (shown to the rider as "last seen 30s ago" rather than silently freezing as if current), and the driver's status reverts to unavailable-for-new-matches until their connection resumes and sends a fresh update.

**5. Would you ever use straight-line distance instead of real routing distance/ETA for the initial candidate ranking?**
Yes, deliberately — straight-line distance is nearly free to compute and good enough to narrow 5M drivers down to the nearest handful; only the final top few candidates are worth paying for a real routing-engine ETA call, which is a much more expensive lookup.

**6. What's the fare-calculation service's failure mode if it's down when a trip completes?**
The trip transitions to `completed` regardless — fare calculation is decoupled and can be retried asynchronously against the trip's recorded distance/time, since blocking trip completion on a pricing service being up would mean a pricing outage traps riders in an already-finished trip.

**7. How do you prevent a rider from being matched to a driver who's still finishing a previous trip?**
The driver's status field is the single source of truth for matchability — it only becomes `available` on the `in_progress -> completed` transition of their current trip, enforced by the same trip state machine, so there's no window where a driver is simultaneously "on a trip" and "available."

**8. Why is location ingestion a separate service from matching, instead of the matching service just reading location updates directly off the stream?**
Because they have entirely different scaling axes: ingestion is provisioned for 1.25M sustained writes/sec and does nothing but validate-and-forward, while matching is provisioned for ~120/sec average reads with a hard latency budget. Coupling them means every matching-service deploy risks the ingestion path, and every ingestion-tier scaling event has to consider matching's latency SLA for no reason — the geospatial index is the deliberate seam between the two.

**9. How would you extend this design to support scheduled rides (booked hours in advance)?**
A scheduled ride doesn't enter the geospatial matching path at request time at all — it needs a separate mechanism (a time-based job, similar to this guide's [Distributed Job Scheduler](../distributed-job-scheduler/00-overview.md)) that triggers the normal `requested`-state matching flow shortly before the scheduled time, when "which drivers are nearby right now" actually becomes a meaningful question. Matching too early would rank against drivers who won't be anywhere near the pickup point by the scheduled time.

**10. Two riders in the same geohash cell request a ride within a second of each other, and there's exactly one available driver between them. Is this fair?**
Not by any explicit fairness policy — it's resolved by whichever request's atomic claim reaches the database first, the same mechanism that resolves every other claim race in this design. Building an explicit fairness/ordering guarantee (first-request-wins by timestamp, say) is a legitimate product decision, but it's a deliberate addition on top of the claim mechanism, not something the atomic update provides for free — worth naming explicitly rather than assuming "first come, first served" is already guaranteed.
