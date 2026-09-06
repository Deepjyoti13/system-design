# Design a Flash Sale System

![500,000 requests funneled down to 1,000 through a waiting room, rate limiter, and atomic counter before ever reaching the durable DB](diagrams/hld.svg)

## Requirements

Sell a strictly limited quantity of a product — say, 1,000 units — starting at a precise announced time, to a far larger number of simultaneous buyers. The defining number here isn't absolute scale, it's the *ratio*: roughly 500,000 requests arriving in the same few seconds against 1,000 units, with a hard requirement that exactly 1,000 succeed — not 999, not 1,001.

## This is a familiar mechanism, at an extreme

This is [Ticket Booking System](../ticket-booking-system/README.md)'s hold-and-timeout mechanism and waiting-room pattern, pushed to the case where nearly all of the traffic arrives in the same instant instead of spread across a browsing session. The seat-claim mechanism itself is identical. What's different is how much of the traffic has to be stopped *before* it ever reaches that mechanism at all.

## Why the naive approach falls over

500,000 requests hitting a single `UPDATE inventory SET count = count - 1 WHERE count > 0` in the same second all serialize on that one row. There are only 1,000 units, so 499,000 of those requests are guaranteed to fail regardless of how the system is built — but if all 500,000 reach the database, the database spends its capacity processing 499,000 failures exactly as expensively as it processes 1,000 successes. The problem isn't correctness (the `WHERE count > 0` guard, same as [the e-commerce schema's inventory race](../../database-design/ecommerce-schema-worked-example.md), still prevents overselling) — it's that the database becomes the bottleneck for traffic that was never going to succeed anyway.

## The real defense: reject as early as possible, layered

Each layer below is cheaper to reject a request at than the layer after it — the entire design is about losing the 499,000 failed requests as early as possible, not about making the database faster.

1. **A virtual waiting room** admits a controlled number of users per second into the actual purchase flow — the same mechanism [Ticket Booking System](../ticket-booking-system/README.md) already uses for its on-sale spike. Most of the 500,000 requests never reach anything stateful at all; they just hold a queue position.
2. **Per-user/IP rate limiting** ([Rate Limiting](../../hld-building-blocks/rate-limiting.md)) stops one script from submitting thousands of attempts per second and consuming a disproportionate share of the waiting room's admitted slots.
3. **An in-memory atomic counter** ([Caching Strategies](../../hld-building-blocks/caching-strategies.md)) absorbs the claim-attempt burst from admitted users far faster than a database row could — a single atomic decrement in Redis, rejecting immediately once it hits zero — with the durable database written through once, or in a small batch, after the fact rather than on every attempt.
4. **The durable database** only ever sees on the order of 1,000 requests — the ones the in-memory counter already let through.

## Interviewer follow-ups

**Why not just let the database handle it if it's ACID and correct anyway?**
Correctness was never the problem — capacity was. The database *would* produce the right answer, eventually, for every one of the 500,000 requests; the issue is that answering "no" to 499,000 of them still costs a row lock and a round trip each, and that cost is what takes the database down. Rejecting the same 499,000 requests one layer earlier, in memory, produces the identical correct outcome for a fraction of the cost.

**How would you handle the in-memory counter and the durable database briefly disagreeing if the process holding the counter crashes?**
Treat the in-memory counter as the fast, authoritative gate for *admission* and the database write as the durable record of *what was actually granted* — on recovery, reconcile by counting confirmed database rows for that product and rebuilding the in-memory counter from that number, the same "recompute from source of truth" pattern this guide's other reconciliation cases use rather than trusting the in-memory value blindly forever.

**How would you prevent bots from occupying most of the waiting room's admitted slots?**
Rate limiting per user/IP is the first layer, but it isn't sufficient alone against distributed bot traffic — a proof-of-work or CAPTCHA-style challenge at waiting-room entry, and per-account (not just per-IP) purchase-attempt limits, raise the cost of running many parallel bot sessions without meaningfully slowing down a genuine single buyer.
