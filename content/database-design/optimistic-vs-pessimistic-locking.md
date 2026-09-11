# Optimistic vs. Pessimistic Concurrency Control

![Two transactions reading the same row and both writing back: the lost update, and the three mechanisms that prevent it](diagrams/optimistic-vs-pessimistic-locking.svg)

[Distributed Locks](../scalability-resilience/distributed-locks.md) covers locking *across* services, where no shared database exists. This page is about the far more common case: two transactions racing for the same row in one database, where the database itself can arbitrate — and about the fact that reaching for a distributed lock here is usually a mistake.

## The race, made concrete

```sql
-- Transaction A                          -- Transaction B
SELECT total_reserved FROM inventory      SELECT total_reserved FROM inventory
  WHERE room_type=7;   -- reads 99          WHERE room_type=7;   -- also reads 99
-- app checks: 99 < 100, room available   -- app checks: 99 < 100, room available
UPDATE inventory SET total_reserved=100   UPDATE inventory SET total_reserved=100
  WHERE room_type=7;                        WHERE room_type=7;
COMMIT;                                   COMMIT;
```

Two bookings, one room, and the counter says 100. This is a **lost update**, and the important thing to notice is that no isolation level below `SERIALIZABLE` prevents it — the danger lives in the gap between the `SELECT` and the `UPDATE`, where application code made a decision on data that went stale before it was written back. `READ COMMITTED` and `REPEATABLE READ` both permit this, so "we use transactions" is not an answer.

## Pessimistic: lock the row before you read it

```sql
BEGIN;
SELECT total_reserved FROM inventory
  WHERE room_type=7 FOR UPDATE;        -- B now BLOCKS here until A commits
-- decide, then write
UPDATE inventory SET total_reserved=100 WHERE room_type=7;
COMMIT;                                -- lock released
```

`FOR UPDATE` takes an exclusive row lock, so the second transaction waits rather than reading stale data. Correct, easy to reason about, and the right tool **when contention is genuinely high** — under heavy contention optimistic approaches spend all their time rolling back and retrying, while a lock queue simply serializes and makes progress.

The costs are real, though. Locks are held **until commit**, so the lock duration is the whole transaction's duration — including any network call to a payment provider you unwisely put inside it. Locking multiple rows in inconsistent orders produces **deadlocks** (the database detects and kills one transaction, which your code must be ready to retry). And a broad `SELECT ... FOR UPDATE` over a date range can lock far more rows than intended, blocking unrelated work.

## Optimistic: detect the conflict at write time

Add a version column, read it, and make the write conditional on it not having changed.

```sql
SELECT total_reserved, version FROM inventory WHERE room_type=7;  -- 99, version 42

UPDATE inventory
   SET total_reserved = 100, version = 43
 WHERE room_type = 7 AND version = 42;      -- ← the guard
-- affected rows == 0 means someone else won: re-read and retry
```

No lock is taken, so readers never block. Checking the **affected row count** is the entire mechanism, and forgetting to check it is how this pattern silently fails in real codebases.

Use a **version counter, not a timestamp**. Timestamps break in two ways: clock skew between application servers, and insufficient resolution — two updates within the same millisecond appear unchanged. A monotonic integer has neither problem.

Optimistic control is the right default when **conflicts are rare**, which is most workloads. It degrades badly when they aren't: under high contention you get a rollback storm where transactions repeatedly do work and throw it away, which is *worse* than blocking because it burns CPU to make no progress.

## The option people skip: make it one atomic statement

Very often the read-modify-write doesn't need to exist at all. Push the decision into the `WHERE` clause and the database does it atomically, with no version column, no explicit lock, and no retry loop:

```sql
UPDATE inventory
   SET total_reserved = total_reserved + 2
 WHERE room_type = 7
   AND total_reserved + 2 <= total_inventory;   -- the business rule, evaluated atomically
-- affected rows == 0 means "not enough inventory" — a normal outcome, not an error
```

A single `UPDATE` statement is atomic and takes its own row lock for the microseconds it runs. The stale-read window is gone because there is no separate read. This is the cheapest correct answer and it covers a surprising share of "we need locking" situations — counters, inventory, balances, rate limits.

You can back it up with a **`CHECK` constraint** as a last line of defence:

```sql
ALTER TABLE inventory ADD CONSTRAINT no_oversell
  CHECK (total_reserved <= total_inventory);
```

Now even a buggy code path that bypasses the guard cannot corrupt the invariant. It's declarative, it can't be forgotten, and its cost is that constraints aren't version-controlled alongside application logic the way code is.

## MVCC changes what you're choosing between

Most modern databases (PostgreSQL, MySQL/InnoDB, Oracle) use **multi-version concurrency control**: writers create new row versions rather than overwriting, so **readers never block writers and writers never block readers.** A plain `SELECT` sees a consistent snapshot regardless of concurrent writes.

Two consequences worth internalizing:

- **Plain reads are already cheap and non-blocking**, so the choice is only ever about the *write* path. Adding pessimistic locks to protect reads is pure cost.
- **MVCC does not prevent lost updates.** A snapshot read followed by an unguarded write is exactly the bug at the top of this page. MVCC gives you a consistent *view*, not a serializable *outcome*.

`SERIALIZABLE` isolation does prevent it — Postgres implements it optimistically (transactions abort with a serialization failure) while other engines use locks — but it costs throughput and still requires retry handling, so it's rarely the pragmatic choice for one hot row.

## `SKIP LOCKED`: locking for queue-shaped work

When many workers claim rows from a table, plain `FOR UPDATE` makes them all queue behind the same first row. `FOR UPDATE SKIP LOCKED` makes each worker take the next *unlocked* row instead:

```sql
SELECT id FROM jobs
 WHERE status='pending' ORDER BY run_at
   FOR UPDATE SKIP LOCKED LIMIT 10;
```

This turns a lock-convoy into clean parallel work distribution, and it's why a relational table is a perfectly good job queue at moderate scale — see the [distributed job scheduler](../case-studies/distributed-job-scheduler/02-lld.md) case study.

## Choosing

| Situation | Use |
|---|---|
| The update is arithmetic on the current value | **One atomic `UPDATE`** with the rule in `WHERE` |
| An invariant must never be violated, whatever the code does | Atomic update **+ `CHECK` constraint** |
| Conflicts are rare; you're editing a whole entity | **Optimistic** (version column) |
| Conflicts are frequent on a hot row | **Pessimistic** (`FOR UPDATE`) — it makes progress instead of retrying |
| Many workers claiming from a work table | `FOR UPDATE SKIP LOCKED` |
| The contended resource is **not** in one database | A [distributed lock](../scalability-resilience/distributed-locks.md) — and only then |

That last row is the point worth ending on: if the data lives in a single database, the database is already a correct, well-tested arbiter with real transaction semantics. Adding Redis-based locking on top introduces a second source of truth, a lease-expiry failure mode, and clock assumptions — to solve a problem `UPDATE … WHERE` already solved. Reach for a distributed lock only when there is genuinely no shared transactional store.

## Interviewer follow-ups

**Why doesn't `REPEATABLE READ` prevent the lost update?**
Because it guarantees your *reads* stay consistent within the transaction, not that the row hasn't changed by the time you write. Both transactions read 99 and both write 100; neither read was inconsistent. Only `SERIALIZABLE`, an explicit lock, or a guarded write closes the gap.

**Your optimistic retry loop is spinning under load. What now?**
That's the signal that contention is high, so the premise was wrong — switch that row to pessimistic locking, or better, restructure to a single atomic `UPDATE` so there's nothing to retry. If the row is genuinely a global hot spot, shard the counter (N sub-counters summed on read) so contention spreads.

**Where does idempotency fit?**
It's orthogonal and both are usually needed. Locking stops *two different* requests from corrupting a row; [idempotency keys](../scalability-resilience/idempotency-keys.md) stop *the same* request, retried, from being applied twice. A double-click needs idempotency; two users racing needs concurrency control.

**Can you hold a lock across a call to a payment provider?**
No. The lock lives for the transaction, so you'd hold a row lock for the provider's entire latency — seconds, or a timeout — serializing everyone behind it. Commit the local state first (a `pending` row), call the provider outside the transaction, then record the result — which is the [saga](../hld-building-blocks/distributed-transactions-saga.md) shape, and why the [payments case study](../case-studies/payments-system/01-architecture-hld.md) is built that way.
