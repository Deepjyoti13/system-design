# Module 04 — Interviewer Q&A

**1. What happens when two requests race for the same last remaining unit at the exact same instant?**
They resolve at the in-memory counter, not the database: a Lua script executed atomically by Redis checks `remaining > 0` and decrements it in the same single-threaded round trip, so only as many claims can ever succeed as the counter had left. The loser's script returns `0` and its handler returns an immediate `409 sold_out` — no lock acquired, no database round trip spent on a request that was always going to fail.

**2. Why not just let the database handle it if it's ACID and correct anyway?**
Correctness was never the problem — capacity was. A single `UPDATE inventory SET count = count - 1 WHERE count > 0` row *would* produce the right answer for every one of the 500,000 requests, eventually. The issue is that answering "no" to 499,000 of them still costs a row lock and a round trip each, and that cost is what takes the database down. Rejecting the same 499,000 requests one layer earlier, in memory, produces the identical correct outcome for a fraction of the cost.

**3. How would you handle the in-memory counter and the durable database briefly disagreeing if the process holding the counter crashes?**
Treat the in-memory counter as the fast, authoritative gate for *admission* and the database's confirmed-order count as the durable record of *what was actually granted*. On recovery, rebuild the counter as `total_units - COUNT(confirmed orders)` rather than trusting whatever the crashed instance last held — the same "recompute from source of truth" pattern this guide's other reconciliation cases use. Any hold that existed only in the crashed process's memory is simply gone; its user sees "sold out" or can retry against the recomputed value, but no unit is ever double-sold.

**4. How would you prevent bots from occupying most of the waiting room's admitted slots?**
Per-user/IP rate limiting is the first layer, but it isn't sufficient alone against distributed bot traffic spread across many IPs and freshly created accounts. A proof-of-work or CAPTCHA-style challenge at waiting-room entry, combined with per-account (not just per-IP) attempt limits, raises the cost of running many parallel bot sessions without meaningfully slowing down a genuine single buyer.

**5. What happens in the single second the sale actually opens — doesn't ~100,000 requests/sec just fall over on arrival?**
That's exactly what the layered gate is built to prevent, and it's why reactive autoscaling isn't the answer here the way it is for organic growth: the sale's start time is known exactly in advance, so the Waiting-Room Gate and rate-limiter tiers are pre-provisioned ahead of it. The gate absorbs the instantaneous spike into a queue with a bounded admission rate; nothing downstream of it ever sees the raw 100,000/sec figure.

**6. Why doesn't a flash-sale unit get its own database row the way a Ticket Booking seat does?**
A seat has an identity someone picks off a map — it needs a row to represent that specific, addressable thing. A flash-sale unit is fungible; nobody cares which of the 1,000 units they get, only whether they got one. Giving each unit its own row would recreate the exact row-contention problem the in-memory counter exists to avoid — the schema only needs to record *outcomes* (confirmed orders), not materialize 1,000 individually-lockable placeholders.

**7. How would you cap purchases at 2 units per account instead of 1?**
The Lua script that currently checks and decrements a single global counter would need to also check and update a second piece of state — a per-account count — atomically in the same script, so the two checks (global remaining, and this account's own count) can't race against each other any more than the global check alone could. Whether that fits in one Redis round trip or needs a compound key is exactly the kind of extension Module 02's practice exercises are built to surface.

**8. What's the failure mode if the payment processor is slow or degraded during the sale itself?**
The processor call is wrapped in a circuit breaker; if it starts timing out systematically, the breaker trips and the service fails fast rather than holding claimed units hostage behind charges that are likely doomed anyway. Because a hold that's never confirmed simply expires and gets released back to the counter by the sweeper, a degraded processor costs some units a delayed sale, not a lost or double-sold one.

**9. Would you ever skip the waiting room and rely on rate limiting plus the atomic counter alone?**
No — rate limiting and the counter both protect a component's own capacity, but neither protects the *user experience* of the other 499,000 people, and at this system's ~500:1 contention ratio that matters even more than it does for [Ticket Booking System](../ticket-booking-system/04-interviewer-qna.md)'s own ~5:1 ratio. Without a gate, 500,000 people get an unpredictable mix of fast responses, timeouts, and cryptic errors; a gate turns that into a legible queue position for everyone, and a bounded, survivable request rate for everything behind it.

**10. How would this design change for a much smaller sale — say, 10,000 units against 20,000 eager buyers, a ratio much closer to Ticket Booking System's own?**
The Waiting-Room Gate becomes unnecessary — it exists purely to handle an extreme, near-instantaneous spike, and a 2:1 ratio spread over even a short window doesn't create the same contention 500:1-in-five-seconds does. The atomic-counter claim mechanism, though, doesn't change at all: it costs nothing extra at lower concurrency and stays correct regardless of scale, which is exactly why it's the right default here rather than an optimization reserved only for the extreme case — the same conclusion Ticket Booking System reaches about its own conditional-update claim.
