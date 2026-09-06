# Module 02 — Low-Level Design

![Trip state machine, and the atomic claim resolving a two-rider race](diagrams/lld.svg)

**Trip state machine** (explicit states, not a boolean):

```
requested -> matched -> in_progress -> completed
                |
                +-> cancelled (rider or driver, only from requested/matched)
```

A transition is only valid from its named predecessor state — `in_progress -> completed` directly from `requested` is rejected, which catches a class of bugs (a duplicate "complete" event replayed out of order) for free.

## Interfaces vs. implementations

- **`LocationIndex`** *(interface)* → **`RedisGeohashIndex`** — `update(driverId, lat, lng)`, `queryNearby(lat, lng, radiusCells)` returning candidate driver IDs from the 9-cell neighborhood. The Matching Service depends on this interface only; swapping the underlying store (a different in-memory engine, a managed geospatial cache) never touches matching logic.
- **`MatchingStrategy`** *(interface)* → **`NearestAvailableStrategy`** — `selectCandidate(candidates, riderLocation)`. Today's strategy is pure nearest-distance; a future two-sided strategy (accounting for driver preferences) or a surge-aware strategy are both just new implementations behind the same interface.
- **`DriverClaimer`** *(interface)* → **`SqlConditionalClaimer`** — `tryClaim(driverId, rideId)` returning a boolean, wrapping the atomic conditional update below. Isolating this behind its own interface means the claim's atomicity guarantee is testable in isolation from the ranking logic that decides *which* driver to try.
- **`FareCalculator`** *(interface)* → **`DistanceTimeDemandCalculator`** — `estimate(pickup, destination)` and `finalize(trip)`, called by the Trip Service without it needing to know how pricing logic works internally.
- **`TripService`** — the orchestrator. Depends on all four interfaces, implements none of the storage or matching logic itself.

## Geospatial lookup, pseudocode

```
function findNearbyDrivers(riderLat, riderLng):
    cell = geohash_encode(riderLat, riderLng, precision=6)
    candidateCells = [cell] + neighboring_cells(cell)
    candidates = []
    for c in candidateCells:
        candidates += locationIndex.queryNearby(c)  # drivers currently registered in this cell
    return candidates.filter(status == "available")
           .sortBy(realDistanceTo(riderLat, riderLng))
```

## Atomic claim, pseudocode

```
function claimDriver(driverId, rideId):
    result = db.execute(
        "UPDATE drivers SET status='claimed', claimed_by=? WHERE id=? AND status='available'",
        rideId, driverId
    )
    return result.rowsAffected == 1   # false means someone else claimed it first
```

`TripService.requestRide` calls `findNearbyDrivers`, then walks the ranked candidates calling `driverClaimer.tryClaim` until one succeeds — the same "try, and fall through to the next candidate on failure" shape as this guide's other claim-based designs, rather than treating the first candidate's failure as the whole request failing.

## Concurrency at the code level

`driverClaimer.tryClaim` needs no in-process lock, and this is worth stating explicitly: the Matching Service runs on many horizontally-scaled instances, so a language-level mutex would only protect against other threads *on the same instance* — it would do nothing about another instance's request claiming the same driver a moment later. Correctness comes entirely from the conditional update being enforced by the database itself (`WHERE status = 'available'`), the same pattern this guide applies everywhere two writers might race for the same row: push the atomicity requirement down into the one system that can actually provide it for free.

The `LocationIndex.update` calls, by contrast, need no coordination at all between concurrent writers — each driver only ever writes their own position, so there's no shared row for two updates to race over. The only subtlety is the read side: `queryNearby` can return a driver whose position is up to 4 seconds stale, which is why the matching service ranks by "real distance at last known position" rather than assuming the read reflects the driver's exact current location — a design choice made explicit in Module 00's non-functional requirements, not an oversight.

## Design patterns you just used, named

- **Repository pattern** — `LocationIndex` and `DriverClaimer` hide storage behind method calls; `TripService` never issues raw queries against the geospatial store or the drivers table directly.
- **Strategy pattern** — `MatchingStrategy` is a strategy: `NearestAvailableStrategy` today, swappable for a surge-aware or two-sided strategy later without touching `TripService`.
- **State pattern (via an explicit enum, not a class hierarchy)** — the trip state machine's enforced transitions are the same discipline this guide applies consistently: model a lifecycle as named states with legal transitions, never as a boolean or free-text field.
- **Retry-with-fallback, not retry-with-repetition** — the claim loop doesn't retry the *same* candidate; it falls through to the next-ranked one, because a failed claim means the candidate is gone, not that the request itself is transiently failing.

## Practice: extend it yourself

Before moving to Database Design, sketch (pseudocode is fine) how you'd add:

1. **Ride pooling / shared rides** — two riders with overlapping routes share one driver. Which interface needs a new method: does `MatchingStrategy` gain a "compatible existing trip" candidate type alongside "available driver," or does this need a new service entirely sitting in front of today's matching?
2. **Driver cancellation after a match** — a driver accepts, then cancels before pickup. Does the trip transition back to `requested` and re-enter matching, or to a new state? What happens to the geospatial index's view of that driver in the meantime — should they immediately become `available` again, or is there a cooldown?

Neither has one clean answer — the point is noticing that the interfaces already drawn (`MatchingStrategy`, the trip state machine) make it obvious which component *should* own each new piece of behavior, even before you've fully worked out what that behavior is.
