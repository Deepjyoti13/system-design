# Design a Ride-Sharing System

## Requirements

**Functional:**
- A rider requests a ride from their current location to a destination.
- The system matches the rider to a nearby available driver.
- Both parties see the other's live location for the duration of the trip.
- Fare is calculated at trip end from distance, time, and demand.

**Non-functional** (stated as assumptions, interview-style):
- 5M drivers with an active app session in a major metro area at peak, each streaming a location update every 4 seconds.
- Match-to-driver latency target: under 3 seconds from ride request to a driver assigned.
- A driver's location must never be more than ~10 seconds stale when a nearby match is being computed.
- Losing a single location update is fine (the next one in 4 seconds supersedes it); losing a trip's fare record is not.

The location stream dwarfs everything else in this system numerically, and that fact should drive almost every infrastructure decision below — it's worth stating out loud before drawing a single box.

## Capacity Estimation

Using this guide's [back-of-envelope method](../../foundations/back-of-envelope-estimation.md):

- **Location-update writes/sec:** 5M drivers ÷ 4 sec/update ≈ **1.25M writes/sec**, sustained, not a spike — this is the number the whole design centers on, not ride-matching volume.
- **Ride requests/sec:** assume 2M rides/day in the metro area → 2M / 86,400 ≈ 23/sec average. At a 5x peak factor (rush hour, an event letting out): **~120/sec peak** — three to four orders of magnitude smaller than the location stream.
- **Location payload:** ~40 bytes/update (driver ID, lat, lng, timestamp, heading). 1.25M/sec × 40B ≈ **50MB/sec** of ingest bandwidth.
- **Location storage, if retained:** most systems only need the *current* position, not history — retaining even 1 hour of raw updates is 1.25M × 3,600 × 40B ≈ 180GB, which is why this data belongs in memory, not a durable log (see Database Design below).

## Approach Walkthrough

Before any boxes: a rider opens the app and sees nearby cars — that view is answered from a geospatial index that's already been kept current by every driver's location stream, not computed fresh per rider. A ride request then asks that same index "who's nearby and free right now," picks one, and confirms — the entire design is really two systems glued together: a high-throughput location-ingestion pipeline, and a comparatively low-throughput matching decision that reads from it.

## API Surface

- `POST /rides/request {rider_id, pickup, destination}` → `{ride_id, status: "matching"}`.
- A persistent channel per driver (WebSocket, see [Long Polling, WebSockets & SSE](../../scalability-resilience/long-polling-websockets-sse.md)) carrying `location.update {lat, lng, heading, ts}` from driver to server, and `ride.offer {ride_id, pickup, fare_estimate}` from server to driver.
- `POST /rides/{id}/accept` — driver accepts an offered ride (idempotent: a duplicate accept from a retry is a no-op, not a second claim).
- `GET /rides/{id}/status` — poll fallback for clients not holding a live channel.

## High-Level Design

![Geospatial 9-cell matching, and the full ingestion-to-fare architecture](diagrams/hld.svg)

**Location-ingestion service.** Every driver's app holds a persistent connection to a fleet of ingestion servers (the same stateful-connection shape as [Long Polling, WebSockets & SSE](../../scalability-resilience/long-polling-websockets-sse.md) describes) and streams `location.update` every 4 seconds. The ingestion tier's only job is to validate and forward each update into the geospatial index — it does not touch the matching decision at all.

**Geospatial index.** Drivers are bucketed by geohash cell (a string prefix that encodes a rectangular lat/lng region — precision length trades cell size against how many drivers land in one bucket). "Find nearby available drivers" becomes: compute the rider's cell, look up that cell plus its 8 neighbors, and scan only the drivers registered in those 9 buckets — a lookup against a few hundred candidates, never a scan of 5M.

**Matching service.** Given the candidate set from the geospatial index, rank by real distance/ETA and attempt to claim the nearest available one. Claiming is the one place this design needs to be careful — see Concurrent-User Handling below.

**Trip service.** Owns the trip state machine (`requested → matched → in_progress → completed`, detailed in Low-Level Design) and is the only writer of the trip's final fare record.

**Pricing/fare service.** Computes a fare estimate at request time (for the rider to see before confirming) and the final fare at trip end from actual distance/time plus a demand multiplier — kept as a separate service because pricing logic changes far more often than the trip state machine does, and shouldn't require redeploying the matching path to tweak a multiplier.

### Load Handling

The dominant load is the 1.25M/sec location stream, not ride requests (120/sec peak is noise by comparison). Ingestion scales horizontally by sharding drivers across ingestion instances by driver ID — adding capacity is adding instances, not a re-architecture. Under extreme load (an ingestion-tier outage in one shard, or a spike from a service disruption reconnecting the whole metro at once), the shedding order is deliberate and asymmetric: first reduce update frequency for drivers who are idle/parked (4s → 15s costs nothing real — a parked car isn't moving), then for active-but-far-from-any-pending-request drivers, and only as an absolute last resort degrade updates for drivers *currently on a trip*, since that's the one feed a rider is actively watching. Load-test target: sustain 1.5x peak ingestion (≈1.9M writes/sec) with p99 geospatial-index staleness still under 10 seconds.

### Concurrent-User Handling

The core race: two ride requests both compute the same driver as nearest-available and both try to claim them at the same instant. Resolution is an atomic claim — a conditional update (`UPDATE drivers SET status='claimed' WHERE id=? AND status='available'`) or a [distributed lock](../../scalability-resilience/distributed-locks.md) with a short TTL, either way checked-and-set in one atomic operation against the driver's status field. The request whose claim lands second sees zero rows affected (or fails to acquire the lock), and is immediately re-matched against the next-nearest candidate from the same cell lookup — not bounced back to the rider as a bare failure. A second, quieter race: a driver's location update arriving while that driver is mid-claim — the claim and the location write touch different fields and don't conflict, but the matching service should treat a driver's position as a snapshot taken at claim time, since re-reading it after the claim could show them already a block away.

## Low-Level Design

![Trip state machine, and the atomic claim resolving a two-rider race](diagrams/lld.svg)

**Trip state machine** (explicit states, not a boolean):

```
requested -> matched -> in_progress -> completed
                |
                +-> cancelled (rider or driver, only from requested/matched)
```

A transition is only valid from its named predecessor state — `in_progress -> completed` directly from `requested` is rejected, which catches a class of bugs (a duplicate "complete" event replayed out of order) for free.

**Geospatial lookup, pseudocode:**

```
function findNearbyDrivers(riderLat, riderLng):
    cell = geohash_encode(riderLat, riderLng, precision=6)
    candidateCells = [cell] + neighboring_cells(cell)
    candidates = []
    for c in candidateCells:
        candidates += geoIndex.get(c)  # drivers currently registered in this cell
    return candidates.filter(status == "available")
           .sortBy(realDistanceTo(riderLat, riderLng))
```

**Atomic claim, pseudocode:**

```
function claimDriver(driverId, rideId):
    result = db.execute(
        "UPDATE drivers SET status='claimed', claimed_by=? WHERE id=? AND status='available'",
        rideId, driverId
    )
    return result.rowsAffected == 1   # false means someone else claimed it first
```

## Database Design & Scaling

![Relational tables plus the non-relational driver_locations hot path](diagrams/er.svg)

- **`drivers`** — id, current status, vehicle info. Small, low write-rate, a normal relational table.
- **`riders`** — id, profile, payment method reference.
- **`trips`** — id, rider_id, driver_id, state, pickup/destination, fare. The durable, ACID-sensitive record of what actually happened.
- **`driver_locations`** — architecturally *not* a normal table. At 1.25M writes/sec of data that's stale in 4 seconds anyway, this belongs in an in-memory store (Redis, holding each driver's latest position plus geohash-cell membership) rather than a durable relational table — the same reasoning [Object / Blob Storage](../../scalability-resilience/object-blob-storage.md) applies to "don't default to the primary DB for a workload with fundamentally different characteristics." Nothing about ride history needs the 4-seconds-ago position once the trip is over.

**Sharding:** `driver_locations` is sharded geographically — a metro area's drivers stay in one shard, matching the geospatial index's own partitioning, so a nearby-driver lookup never has to fan out across shards (see [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md)). `trips` is sharded by `rider_id` or `trip_id` instead, since trip history is queried per-user, not per-geography, and geographic sharding would scatter one rider's trip history across every metro they've ever ridden in.

**Indexes:** `trips(rider_id, created_at)` for ride-history pagination; `trips(driver_id, created_at)` for a driver's earnings view. No index on `driver_locations` in the relational sense — the geohash bucket structure in Redis *is* the index.

## Interviewer Q&A

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
