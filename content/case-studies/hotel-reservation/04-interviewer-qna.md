# Module 04 — Interviewer Q&A

---

### 1. Two users book the last room at the same time. How do you stop the double-booking?

First, the two races that get conflated need separating, because they need different mechanisms:

- **One user, two clicks** → solved by an **idempotency key**.
- **Two users, one room** → solved by **concurrency control**.

Shipping only one of them leaves a real bug.

For the second: the naive flow is a `SELECT` to check availability, then an `UPDATE`. That's a **lost update**, and the critical fact is that **no isolation level below `SERIALIZABLE` prevents it.** `READ COMMITTED` and `REPEATABLE READ` both permit it, because nothing was read inconsistently — both transactions read a genuinely-committed 99. The danger lives in the *gap* between the `SELECT` and the `UPDATE`, where application code decided on data that went stale before it was written back. So "we use transactions" isn't an answer.

The fix is to eliminate the gap — put the business rule in the `WHERE` clause:

```sql
UPDATE room_type_inventory
   SET total_reserved = total_reserved + :n
 WHERE hotel_id = :h AND room_type_id = :t
   AND date >= :start AND date < :end
   AND total_reserved + :n <= total_inventory * 1.10;   -- the rule, evaluated atomically

-- affected_rows < nights_requested  ⇒  some night is full  ⇒  ROLLBACK, return 409
```

There's no read to go stale, so the lost update is **structurally impossible** rather than detected. No version column, no retry loop. And the affected-row count carries the multi-night semantics for free — fewer rows updated than nights requested means a specific night was full, which is exactly what the `409` needs to say.

I'd keep a `CHECK (total_reserved <= total_inventory * 1.10)` constraint as a backstop too — not because the `UPDATE` needs it, but because it's the only mechanism **no code path can bypass**: a future service, a data migration, or a manual `UPDATE` during an incident all hit it.

---

### 2. Would you use pessimistic or optimistic locking?

Neither, primarily — but the reasoning for both matters.

**Pessimistic (`SELECT … FOR UPDATE`)** is correct and is genuinely the best option **under heavy contention**, because a lock queue makes steady progress while optimistic approaches burn CPU on rollbacks. Its problems: locks are held for the whole transaction (so nothing slow can be inside it — which is precisely why payment is moved out), and a multi-night booking locks several rows, so two overlapping stays requested in different date orders can **deadlock**.

**Optimistic (a `version` column)** is faster when conflicts are rare, which describes most of this system's traffic — most hotels on most dates have plenty of rooms. It degrades badly under contention into a **rollback storm**, which is worse than a lock queue because it burns CPU to make no progress. And that's exactly the peak-season hot-row scenario. Also: use a version *integer*, never a timestamp — clocks skew between app servers and two updates in the same millisecond look unchanged.

The reason I'd use the **atomic conditional `UPDATE`** instead: its lock is held for the duration of one statement — microseconds — so there's no meaningful contention window left to queue on. Pessimistic locking wins when the *transaction* is long, and the whole point of moving payment out was to make it short. Having done that, the lock-free option is strictly better.

**When pessimistic would be right:** if the decision needed application logic SQL can't express — a fraud check, a loyalty-tier calculation, or a multi-room-type group booking with cross-type rules. Then you need the read and write in one guarded window and `FOR UPDATE` is the tool. Group bookings are a real unbuilt gap here, so that's not hypothetical.

---

### 3. Why not `SERIALIZABLE` isolation?

It would work. It prevents the lost update outright.

But it costs throughput on **every** transaction in the database, not just contended ones; in PostgreSQL it's implemented optimistically, so you still get serialization failures and still need retry handling; and it's a database-wide setting deployed to fix a problem localized to one row's update.

The atomic conditional `UPDATE` solves it for the price of one `WHERE` clause. Reaching for `SERIALIZABLE` is using a global lever on a one-statement problem.

---

### 4. Where does the 10% overbooking allowance live?

**Inside the atomic condition** — `total_reserved + n <= total_inventory * 1.10` — which is the point worth making rather than the number.

If it lived in application code after a read, it would be exactly the check-then-act race from answer 1. Putting it in the `WHERE` clause means **the business rule and the atomicity are the same expression**, so you cannot forget to apply the limit — it's the thing making the write conditional. Plus the `CHECK` constraint as an unbypassable backstop.

Worth flagging that the invariant here is *not* `reserved <= total`. The hotel deliberately wants to oversell, because cancellations are predictable in aggregate and a fuller hotel is more profitable. So "prevent overselling" is the wrong framing — the requirement is "oversell by exactly the permitted amount and no more," which is a business rule that has to live somewhere trustworthy.

The honest limitation: a flat 10% is a placeholder for a model. Real revenue management sets it per hotel, per date, per room type, from historical cancellation rates — a resort in peak season cancels very differently from an airport hotel on a Tuesday. And making it dynamic is genuinely awkward, because a per-row multiplier can't live in a static `CHECK`, so the backstop would have to become a trigger or disappear — losing the defence-in-depth that justified keeping it.

---

### 5. Why is this a relational database? Everything else in this guide uses NoSQL somewhere.

Because the requirements point at it, and it's worth doing the arithmetic first:

```
1,000,000 rooms × 70% occupancy ÷ 3-night average stay ÷ 86,400
= ~2.7 reservations/sec
```

**Under three writes per second.** Reads are ~1,000:1 above that, so ~3,000/sec at the top of the funnel — and hotel data is near-static, so it caches almost perfectly. The inventory table is 5,000 hotels × 20 types × 365 days × 2 years = **73 million rows, under 4 GB.**

So there is **no throughput problem to solve.** NoSQL's strength is write scalability, which is the one thing not needed. What *is* needed is multi-row atomic transactions and declarative constraints — which is exactly what ACID provides.

The framing I'd use: **this is a correctness-under-concurrency problem at low volume**, which is a genuinely different problem from the high-throughput designs elsewhere. Reaching for a distributed store here would be building infrastructure to solve a problem that doesn't exist, and giving up the transaction that makes the double-booking guarantee free.

---

### 6. Wouldn't a proper microservice architecture separate inventory from reservations?

It would, and that's the thing you must not do here.

Creating a reservation and decrementing inventory **must be atomic.** Split across two services with two databases, there's no transaction covering both, so you'd need either [2PC](../digital-wallet/02-distributed-transactions.md#option-1-two-phase-commit) — blocking, lock-holding, with a coordinator that can freeze both participants — or a [saga](../../hld-building-blocks/distributed-transactions-saga.md) with compensation, which means a window where inventory says a room is free and a reservation for it already exists.

**Both trade correctness for a distribution that 3 reservations/sec doesn't need.**

So the service boundary is drawn where the transaction boundary is, and the transaction boundary is drawn by the correctness requirement. Everything else *is* split — hotel service, rate service, payment service, admin service — on real boundaries: different change cadences, different data shapes, different team ownership (rates belong to revenue management, with their own tooling and approval flow).

The principle: **split when the reasons to change differ, not when the nouns differ.** "Reservation" and "inventory" are two nouns and one invariant.

When would you split them? If inventory reads needed independent scaling — the Booking.com scenario. Even then the better first move is a read replica or a cache, not a service boundary, because the *write* path is what needs the transaction and the write path is tiny.

---

### 7. Where does the payment call go — inside the transaction or outside?

**Outside**, and it's the most important structural decision on the booking path.

A payment provider takes hundreds of milliseconds to seconds and can time out. Inside the transaction, the inventory row lock would be held for that entire duration — **serializing every other booking for that hotel and room type behind one slow payment.** At peak season with real contention on a popular hotel, that turns a trivially-loaded system into an unusable one.

So the flow is: commit the reservation as `pending` with inventory already decremented, call the payment provider, then a second transaction confirms or releases. That's the [saga](../../hld-building-blocks/distributed-transactions-saga.md) shape.

The cost is a **`pending` state that must be reconciled**, and the sweeper that does it is the design's most important background job. `pending` reservations *hold inventory*, so a checkout abandoned at the payment step has already decremented the counter. Without a sweeper that inventory is held forever — and the failure mode is availability slowly leaking away, presenting as "the hotel looks full and isn't" with nothing in the logs.

Which is why the alert isn't on the sweeper's completion, it's on **inventory currently held by `pending` rows.** That's the number that tells you the mechanism is working.

The unresolved part: the `pending` timeout is a guess. Too short and a slow-but-successful payment gets its inventory released underneath it, leaving a paid booking with no room. Too long and a failed checkout holds the last room of a sold-out hotel for an hour. The right value depends on the PSP's p99.9 latency, which nobody has measured here.

---

### 8. A guest books "a king room". Which room do they get?

Whichever one the front desk assigns at check-in — and this is a data-model question disguised as a product one.

**Guests book a room *type*, not a room.** That differs from Airbnb (where you book a specific listing) and from [ticket booking](../ticket-booking-system/00-overview.md) (where you book seat J14), and it changes the unit of inventory from "a room" to **"a (hotel, room type, date) triple with a count."**

The consequence is significant. Modelling it per-physical-room would force the booking transaction to *pick* a room — a scan plus a decision, with concurrent bookings contending over arbitrary specific rows. And assigning room 412 at booking time removes the front desk's flexibility to reshuffle for maintenance, adjacency requests, or an early checkout.

Modelling it as a **counter** makes booking a single arithmetic update — and that's precisely what makes the atomic conditional `UPDATE` from answer 1 available at all. **The data model choice is what unlocked the simplest correct concurrency control.** A seat, being a unique resource with a binary state, can't use that technique and has to lock a specific row.

The `room` table still exists — check-in needs it, housekeeping needs it. It just isn't involved in booking.

---

### 9. Now make it Booking.com — 1,000× the traffic.

Three changes, in order.

**Shard on `hotel_id`.** Every query filters on it, so the shard is computable from the request: single-shard reads and writes, always. And critically, **no transaction ever spans two hotels** — a booking touches exactly one hotel's inventory — so there are **no distributed transactions at all.** At 30,000 QPS across 16 shards that's ~1,875 QPS each, comfortable for a single cluster.

That's an unusually clean sharding story, and it's a property of the domain rather than of the design. The alternatives all fail: sharding on `date` puts peak season on one shard; sharding on `reservation_id` scatters one hotel's inventory across every shard and makes the booking transaction distributed.

**Add a CDC-fed inventory cache.** `{hotel}_{type}_{date} → available_count` in Redis, populated from the database's replication log. The essential property: **the cache is for display only; the database remains the authority.** So a stale cache means a user sees "1 room left" and then gets a `409` — which is *identical* to what already happens when someone hesitates while browsing. It **cannot** cause an oversell, because the atomic `UPDATE` and the `CHECK` constraint are still the gate. That's why cache inconsistency is tolerable here and would be unacceptable in the [digital wallet](../digital-wallet/05-db-design.md#consistency): the cache never participates in the decision.

**Archive old reservations.** Reservation volume grows forever while the *useful* set is bounded by the booking horizon.

The remaining cost worth naming: the hot-row problem doesn't go away. Every booking for one hotel, one room type, one date hits **one row**, and no amount of sharding helps because the contention is on a single logical key. The mitigation is the sharded-counter trick from [like counting at scale](../../../like-counting-at-scale/00-overview.md) — split a room type's inventory into N sub-counters summed on read — at the cost of a scatter-read for availability and a rebalancer so one sub-counter doesn't run dry while others have space.

---

### 10. What's the biggest weakness?

Three, and I'd lead with the one that isn't a feature gap.

**The design deliberately doesn't scale, and the single ACID primary is a hard single point of failure for all booking.** If it fails, replicas serve browsing and availability but **nobody can book.** That's the accepted cost of choosing one ACID primary over a distributed store — correctness over availability, taken deliberately — and it's the right call at 3 reservations/sec. But it should be stated as a choice with a consequence, not as an oversight, and it's the thing that changes first under the Booking.com pivot.

**The `pending` reconciliation sweeper is load-bearing and its timeout is unpriced.** Covered in answer 7: `pending` reservations hold inventory, so a sweeper failure leaks availability silently, and both timeout directions have real failure modes. This is a background job whose correct operation is invisible and whose failure is also invisible — the worst combination.

**The oversold-by-admin case has no workflow.** An admin can reduce `total_inventory` below `total_reserved` when a room goes out of service, leaving a legitimately oversold row. The system correctly **stops making it worse** (new bookings fail the `WHERE` clause) and honours existing reservations rather than retroactively cancelling one. Then a human has to relocate a guest — and there's no tooling, no notification, no record of who and why, and no prioritisation of *which* guest. That's where the actual operational pain of a hotel system lives, and it's entirely unmodelled.

Also worth naming briefly: **no multi-room-type or group bookings** (a `reservation` row references one `room_type_id`, so "two kings and a twin" is three separate reservations with no all-or-nothing guarantee — and fixing it means a wider transaction with a lock-ordering rule to avoid deadlock), and **no rate history** (an update overwrites the price for a date, so "what was this advertised at last Tuesday?" is unanswerable, which matters for disputes and revenue analysis).
