# Module 04 — Interviewer Q&A

**1. What happens when two requests hit the same resource at the same instant?**
Two clients both call `hold()` for the same seat: the atomic conditional `UPDATE seats SET status='held' ... WHERE seat_id=? AND status='available'` only ever succeeds for whichever request's transaction commits first — the second affects zero rows and gets an immediate `409`, with no lock ever acquired by either request.

**2. What happens when traffic spikes 10x for an hour?**
This system's real spike isn't "10x for an hour" — it's closer to 1000x for the first 60 seconds of an on-sale, then near-zero. The Waiting-Room Gate is exactly the mechanism that turns that shape into something the rest of the system never has to handle directly: it admits a bounded rate regardless of how many people arrive, so contention on the seat-hold claim stays bounded by design.

**3. Why not just use a distributed lock (Redis, Zookeeper) per seat instead of a conditional UPDATE?**
A lock has to be explicitly acquired and explicitly released, including on the crash/timeout path — that's an extra failure mode (stale locks, lock-service outages) purely to reimplement what the database's own row-level atomicity already provides. The conditional update *is* the lock, expressed as a single round-trip with no separate release step.

**4. How do you decide how long a hold should last?**
It's a trade-off named explicitly in Architecture & HLD: too short, and legitimate users lose seats mid-checkout to friction (slow typing a card number, a 3D-Secure redirect); too long, and abandoned seats sit unavailable for other buyers during exactly the window when demand is highest. ~8 minutes is a reasonable default; the actual number should come from checkout funnel data (how long does payment genuinely take?) rather than a guess.

**5. What happens to a seat if the user's payment fails partway through — card declined, network timeout, or the hold simply expires mid-payment?**
A hard decline releases nothing immediately — the seat just stays held until its own `hold_expires_at`, deliberately, so the same user can immediately retry with a different card without losing the seat to someone else. A network timeout is treated as unknown, not failed — the charge's idempotency key makes retrying safe. An expiry mid-payment is caught by the confirm transaction's own re-check (Module 02) — the read at the start of confirmation isn't trusted as the final word.

**6. How would you prevent bots from grabbing all the good seats the instant they go on sale?**
Not fully solved by this design — it's a fairness/abuse problem layered on top of the concurrency problem this case study focuses on. What this design does provide is a chokepoint (the Waiting-Room Gate) where bot-detection signals (CAPTCHA, rate-limiting by IP/account, proof-of-work) could be applied before a session is even admitted into the flow, rather than needing to be bolted onto every downstream endpoint separately.

**7. How would you support booking multiple seats together in one transaction?**
Named explicitly as a practice exercise in Module 02: the single-seat conditional update doesn't directly generalize to "claim these 5 seats, all-or-nothing." The real design work is making that claim atomic across multiple rows — either a single transaction claiming all 5 with the same conditional `WHERE`, checking that all 5 updates succeeded before committing, or rolling back (re-releasing) the ones that did succeed if even one didn't.

**8. Why record the booking and the outbox event in the same transaction, instead of just calling the notification service directly?**
A direct call couples the booking's success to the notification provider's availability — a flaky email provider could make a successful, paid booking look like it failed, or worse, make the code look like it needs to roll back a booking because a *notification* failed. The outbox decouples them completely: the booking transaction only needs the database to succeed; the relay handles notification delivery, retries, and provider outages entirely on its own schedule.

**9. What happens if the scheduler/sweeper is itself down for a while?**
Nothing breaks — it degrades gracefully. Held-but-expired seats simply stay marked `held` longer than they should, meaning fewer seats are available for new browsers than truly should be, but no seat is ever double-sold and no booking is ever lost, because the sweeper only ever performs the release side of the mechanism, never the claim side. Once it resumes, its normal query picks up every expired hold, no different from a job scheduler catching up on missed runs.

**10. How would this change for a much smaller, low-demand event — say, 50 seats and no on-sale spike at all?**
The Waiting-Room Gate becomes unnecessary — admission control exists purely to handle the spike, and a 50-seat event with steady, spread-out demand never creates the same contention. The conditional-update claim mechanism, though, doesn't change at all: it costs nothing extra at low concurrency and remains correct regardless of scale, which is exactly why it's the right default rather than an optimization reserved for "the big case."
