# Design a Ticket Booking System

![Seat state machine (available/held/booked/expired) and a waiting-room queue gating a traffic spike](diagrams/hld.svg)

## Requirements

**Functional:** browse available seats for an event, select seats, hold them temporarily while paying, confirm the booking on successful payment, and release the hold automatically if payment isn't completed in time.

**Non-functional** (stated as assumptions, interview-style): a popular event's on-sale moment can see 100K users all trying to book from a few thousand seats within seconds — that's the system's defining hard case, not its steady-state average load. A held seat must never be sold to two people, and a seat's hold must expire and become available again without manual intervention.

## The core race — and why it's harder than typical inventory

This guide's [e-commerce schema](../../database-design/ecommerce-schema-worked-example.md) already solves the "don't oversell a unit of inventory" race: wrap a conditional decrement and the order insert in one transaction, so `WHERE count > 0` closes the race at the database level. A ticket seat needs that same atomicity, but it isn't done the instant it's claimed.

A customer who taps a seat needs several minutes to enter payment details. During that window, the seat can't be available to anyone else, but it also isn't sold yet — someone has to give it back if the customer abandons the flow. That's a **reservation with a timeout**, not a one-shot atomic decrement. The e-commerce schema's mechanism is necessary here but not sufficient on its own.

## The hold mechanism

Claiming a seat is the same conditional-update trick as the e-commerce inventory decrement, extended with an expiry stamp:

```
UPDATE seats
SET status = 'held', held_by = ?, hold_expires_at = NOW() + INTERVAL '8 minutes'
WHERE seat_id = ? AND status = 'available'
-- 0 rows affected: someone else already holds or booked this seat
```

The `WHERE status = 'available'` is what makes this safe under concurrency, for the identical reason the e-commerce page's `WHERE count > 0` is: the row's own current value is part of the condition, so two simultaneous claims on the same seat can't both succeed — exactly one `UPDATE` matches, the other affects zero rows.

Releasing an expired hold needs a **sweep**: a background job scanning for `status = 'held' AND hold_expires_at < NOW()` and flipping those rows back to `available`, or a TTL-based approach (the same lazy-expiry idea this guide's [caching strategies](../../hld-building-blocks/caching-strategies.md) page uses for cache entries) where a read that encounters a stale hold reclaims it on the spot instead of waiting for a sweep to get there.

Hold duration is a real trade-off, not a number to pick arbitrarily: too short frustrates a genuine buyer who's slower to check out; too long lets one non-paying browser tab block a seat from someone who'd have actually completed the purchase. Most real systems land somewhere around 5–10 minutes and treat it as a tunable, not a constant.

## Handling the on-sale spike

100K users arriving in the same few seconds isn't a capacity problem you scale your way out of by adding more seat-hold endpoints — it's a **flow-control problem**. The mechanism real ticket systems use is a virtual waiting room in front of the booking flow itself: admit a controlled number of users per second into actual seat selection, and queue everyone else with a position/estimated-wait indicator. This is [rate limiting](../../hld-building-blocks/rate-limiting.md) and [load shedding](../../scalability-resilience/backpressure-load-shedding.md) applied at the *product* layer, not just the API layer — the goal isn't to protect one endpoint from overload, it's to guarantee that whoever *is* admitted gets a seat-selection experience that actually works, instead of 100K browsers all racing the same few thousand rows at once.

## Interviewer follow-ups

**What happens if a user closes their browser tab mid-payment — how does their held seat get released without waiting the full timeout?**
It doesn't have to wait if you don't want it to: a heartbeat from the client (or a WebSocket disconnect signal) can trigger an early release, same as any session-liveness check. In practice most systems don't bother — the hold timeout is short enough (a few minutes) that an abandoned tab self-heals on its own, and building an early-release path is optimizing a case the timeout already handles adequately.

**Would you show seat availability in real time to browsing users, and what does that cost under heavy load?**
Showing it live means every browsing (not yet holding) user is polling or subscribing to seat-status updates for the whole map — a read load that dwarfs the actual booking traffic, since far more people browse than book. A cheaper compromise: show a coarser "many/some/few left" signal to browsers, and only fetch exact per-seat status once a user is actually inside the (already-throttled) booking flow.

**How would you prevent bots/scalpers from holding large blocks of seats?**
Cap holds per account/session (a rate limit on the hold endpoint itself, per identity, not just per IP), and treat the waiting-room admission as the first checkpoint for bot detection (CAPTCHAs, device fingerprinting, account-age heuristics) rather than trying to distinguish bots from humans at the seat-hold step where the atomicity mechanism has to stay simple and fast.

**Would a distributed lock work instead of the conditional-update mechanism?**
It could, but it's the wrong tool here: a [distributed lock](../../scalability-resilience/distributed-locks.md) is for coordinating a multi-step critical section across services, and it adds a whole failure mode (lock expiry mid-hold, fencing) that a single conditional `UPDATE` on one row doesn't have. The database's own row-level atomicity already gives the correctness a seat claim needs — reaching for a lock on top of it would be solving a problem this design doesn't have.
