# Module 00 — Overview

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
