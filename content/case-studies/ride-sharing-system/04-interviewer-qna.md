# Module 04 — Interviewer Q&A

**What happens when two ride requests try to claim the same driver at the same instant?**
The atomic conditional update (`WHERE status='available'`) means only one claim's `UPDATE` affects a row; the other gets zero rows affected and is immediately re-matched to the next-nearest available driver from the same candidate set, not returned to the rider as an error.

**What happens when traffic spikes 10x for an hour — a concert or a stadium event ending?**
Ride-request volume at 10x is still small in absolute terms (~1,200/sec, trivial for the matching service), but it's *geographically concentrated*, which spikes the number of riders and drivers in the same handful of geohash cells. The real risk is contention on a small set of "hot" cells, not raw throughput — mitigated by finer-grained cells in known high-density zones and by the matching service backing off to a slightly wider cell radius if the immediate 9-cell neighborhood is momentarily saturated with claim conflicts.

**Why bucket by geohash instead of just running a radius query against a spatial index in the primary database?**
A database spatial index (R-tree/PostGIS-style) works, but at 1.25M writes/sec it would mean 1.25M index updates/sec against a durable store — the geohash-in-Redis approach keeps that write rate in memory, where it belongs, and only ever persists a *trip*, which is orders of magnitude rarer.

**How would you handle a driver's app losing connectivity mid-trip?**
The trip stays `in_progress` — the connection dropping isn't a state transition. The last known location simply stops updating (shown to the rider as "last seen 30s ago" rather than silently freezing as if current), and the driver's status reverts to unavailable-for-new-matches until their connection resumes and sends a fresh update.

**Would you ever use straight-line distance instead of real routing distance/ETA for the initial candidate ranking?**
Yes, deliberately — straight-line distance is nearly free to compute and good enough to narrow 5M drivers down to the nearest handful; only the final top few candidates are worth paying for a real routing-engine ETA call, which is a much more expensive lookup.

**What's the fare-calculation service's failure mode if it's down when a trip completes?**
The trip transitions to `completed` regardless — fare calculation is decoupled and can be retried asynchronously against the trip's recorded distance/time, since blocking trip completion on a pricing service being up would mean a pricing outage traps riders in an already-finished trip.

**How do you prevent a rider from being matched to a driver who's still finishing a previous trip?**
The driver's status field is the single source of truth for matchability — it only becomes `available` on the `in_progress -> completed` transition of their current trip, enforced by the same trip state machine, so there's no window where a driver is simultaneously "on a trip" and "available."
