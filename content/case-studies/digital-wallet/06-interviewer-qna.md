# Module 06 — Interviewer Q&A

---

### 1. Two accounts on two shards. How do both legs happen or neither?

**TC/C — try-confirm/cancel** — and the reason for rejecting the two obvious alternatives matters more than the choice.

**Not 2PC**, for two reasons. It holds row locks between `PREPARE` and `COMMIT`, so a hot merchant account would be locked essentially continuously at 1M TPS. And more seriously, **it's a blocking protocol**: if the coordinator dies after both participants vote "yes" but before sending `COMMIT`, both sit with prepared transactions and **locks held indefinitely.** They can't decide alone — committing risks a mint if the other aborted, aborting risks losing a committed transfer — so both accounts freeze until the coordinator returns.

**TC/C's key difference:** in 2PC, phase 1 leaves transactions *un-committed*. In TC/C, **phase 1 fully commits.** Each partition's local transaction begins and ends in microseconds, nothing is ever held, and a coordinator crash leaves no one blocked — just an unfinished transfer that a recovery worker drives to a terminal state.

**Not Saga**, though for a two-leg transfer they're nearly identical. TC/C wins on two counts: its phases are parallelizable, which matters for multi-leg transfers (a 5-way split payment confirms all five at once rather than sequentially), and its `Try` phase naturally expresses **reservation** — "hold $10" is a weaker, useful operation that Saga has no vocabulary for.

What TC/C gives up is **atomicity**: there's a real millisecond window where only the debit landed and the system's total is short. Three things make that safe, and the third is what actually closes the argument: the window is bounded by one partition round trip; debit-first means it's a *deficit* nobody can spend; and audits read the event log **at a committed sequence position**, so they never observe the window at all.

---

### 2. Why does the debit have to happen first?

Because the two orderings are not symmetric, and only one is recoverable.

```
Debit first:    A: 100→90   [system total $10 SHORT]   C: 50→60
Credit first:   C: 50→60    [system total $10 OVER ]   A: 100→90
```

In the credit-first window, **C can spend $10 that A still has.** If both spend, the system has paid out money it never had — a mint, and unrecoverable, because you can't claw back a completed downstream payment.

In the debit-first window the money is temporarily nowhere. Nobody can spend it. If the transfer fails, compensation returns it to A. Worst case, a user briefly sees a lower balance.

**A temporary deficit is recoverable; a temporary surplus is exploitable.**

The consequence people miss: this also **forbids running the legs in parallel**, because parallelism would create a window where the credit might land first. That's a real latency cost paid for a correctness reason, and it's a constraint on any protocol you pick rather than a property of TC/C.

---

### 3. The credit leg times out. Do you compensate?

**No — you record uncertainty.** This is the trap in the whole design.

On timeout you do **not** know whether the leg applied; the request may have committed and the response been lost. Both intuitive responses mint money:

- *Assume it failed, compensate the debit* → if the credit actually succeeded, you've credited money back that was never removed. **Mint.**
- *Assume it succeeded, mark complete* → if it actually failed, the recipient was credited without the sender being debited. **Also a mint.**

So the coordinator writes `state = UNCERTAIN` to `phase_status` and returns `503`. A recovery worker later resolves it by **reading `applied_legs` on the partition** — the authoritative record of whether `(transaction_id, leg_number)` was applied.

That's why partitions expose idempotency on `(txn, leg)` rather than the coordinator merely tracking its own attempts: **the coordinator's memory of what it did is not trustworthy; the log is.** And it's why `applied_legs` has to live *inside the replicated log* rather than in leader memory — on failover, a new leader with no record would happily apply a retried leg twice.

---

### 4. Why event sourcing? Isn't `UPDATE balance` simpler?

Simpler, and it can't satisfy the requirements. Under `UPDATE accounts SET balance = 90`, the information that A had 100 is **gone.** You can log the change beside it, but now you have two sources of truth that can disagree, and when they do the log isn't authoritative — so you can't use it to correct the balance.

The three audit questions each become unanswerable:

- *Balance last March 3rd at 14:00?* → Unknown.
- *How do we know today's balance is right?* → You don't; there's nothing to check against.
- *Is the logic still correct after this change?* → Unanswerable, because the inputs are gone.

Store the delta instead — `Debited(A, 10, txn=T1, seq=41)` — and the balance becomes a **fold over history**. Every question is answerable: fold to a timestamp; recompute from event 0 and compare; replay the same events through new code and diff.

The framing that makes it click: **events are facts, state is a conclusion.** Facts don't change, so history can't be rewritten. Conclusions are recomputable, so a bug in the folding logic is fixable *retroactively* — fix the code, replay. Under the balance-storing schema, a bug that produced a wrong balance is permanent.

And a benefit that wasn't the motivation but pays for itself: **you can build new read models for data that predates them.** A monthly-statement feature invented today works for the last three years. Commercially that's often worth more than the auditing.

---

### 5. Command versus event — why does the distinction matter?

Because **you cannot replay commands, only events**, and that single fact determines what has to be durable.

| | Command | Event |
|---|---|---|
| Tense | *"transfer $10"* | *"$10 was debited"* |
| Can fail? | **Yes** | No — it happened |
| Deterministic? | **No** | Yes |
| Durable? | Optional | **Yes — the source of truth** |

Replaying "transfer $10" re-evaluates "does A have $10?" against whatever state exists now, and might call a fraud API that answers differently. That doesn't reconstruct history — it **re-decides** it. Replaying `Debited(A, 10)` always subtracts 10.

So the state machine is forbidden from reading the clock, using randomness, or doing I/O inside `apply()`. Timestamps arrive *inside* the event, stamped once at command time:

```
handle(command):        # runs ONCE, may be non-deterministic
    emit Debited(..., at=now(), fraud_flag=fraudService.check(command))

apply(event):           # runs on EVERY replay — pure function
    account.balance -= event.amount
```

[Module 04](./04-lld.md#interfaces-vs-implementations) encodes this in the type system: `handle` gets a read-only `StateView`, `apply` gets `MutableState` and an event and has no handle to call anything external. A single `now()` inside `apply()` silently breaks reproducibility, and you find out months later when an audit returns the wrong number.

The payoff: **only the event log needs to be reliable.** Commands can be lost; state and snapshots are regenerable. That's what lets all the durability effort concentrate on one append-only structure — which is also the fastest to write and cheapest to replicate.

---

### 6. One database node does 1,000 TPS. You need 2,000,000. Do you need 2,000 nodes?

That's the right question to ask, and the answer is that **2,000 nodes each holding a shard of the world's money is an operations disaster**, so the goal becomes raising per-node throughput rather than adding nodes. Every 10× removes an order of magnitude of machines:

| Per-node TPS | Nodes at peak |
|---|---|
| 1,000 (typical managed RDBMS) | 2,000 |
| 100,000 | **20** |

Four optimizations get to ~100k TPS/node — **20 partitions, 60 machines at RF 3, a 33× reduction:**

1. **Sequential append instead of random update.** ~150 MB/s versus ~0.6 MB/s — the same 244× gap. At 200 bytes/event that's 750k events/sec theoretical, so **the disk stops being the bottleneck**, which tells you to optimize CPU and network next.
2. **State in memory.** 100M accounts × 64 bytes ≈ 6.4 GB per partition. No disk read to validate a command. Only safe because state is *derivable* — a conventional design can't do this, since there the in-memory balance is the only copy.
3. **`mmap` the event file.** An append becomes a `memcpy`, removing 100k syscalls/sec. Acceptable only because durability comes from Raft replication rather than local `fsync`.
4. **A single thread pinned to a core.** Counter-intuitive, but: no locks, no context switches, hot cache — and **serial execution becomes the concurrency control**, which is why two concurrent debits on one account need no lock. Multi-core capacity comes from running multiple partitions per machine.

The reframe worth leading with: this isn't a sharding problem, it's a "make one node fast enough that you barely need to shard" problem.

---

### 7. Two transfers debit the same account at once. How do you prevent overdraft?

**No lock at all** — the partition's Raft leader is a **single-threaded writer** processing its command queue serially. Validation and event append are one atomic step in that sequence, so the second command validates against state the first already updated, and correctly fails with `402` if funds are now insufficient.

**Serial execution replaces locking**, which is the real reason for the pinned-thread design in answer 6 — the throughput optimization and the concurrency-control mechanism are the same thing.

Contrast the alternatives, and why they're worse here: pessimistic `SELECT … FOR UPDATE` holds a lock for the transaction's duration and deadlocks under multi-row transfers; optimistic version-column checking spends CPU on rollbacks under contention. Both are the right answer in a general database ([Optimistic vs Pessimistic Concurrency Control](../../database-design/optimistic-vs-pessimistic-locking.md)) and both are unnecessary when there's exactly one writer.

Two things that *do* need care: `apply()` must run only **after** `raft.propose()` commits, or the leader's memory would reflect an event a subsequent election discards; and a user double-clicking is a different race entirely, handled by the mandatory idempotency key rather than by concurrency control.

---

### 8. A user transfers money, refreshes, and sees the old balance. Fix it.

That's CQRS's eventual consistency showing through, and it's a real trust problem rather than a cosmetic one — "I sent money and it didn't work" is a support ticket.

The cause: balances are served from a **projection** that folds the event stream asynchronously. Transfers commit to the log; the projection catches up milliseconds later.

Three fixes, and I'd ship the third:

1. **Expose the boundary.** Every balance response carries `as_of_sequence`, and the transfer response returns its committed sequence. A client that knows its transfer was sequence 8,421,008 can *tell* that a balance at 8,421,001 predates it. Exposing the consistency boundary beats hiding it.
2. **Read-your-writes on demand.** The client passes `?min_sequence=8421008`; the read model waits briefly or returns `409` to retry.
3. **Read the write path for the account you just touched.** Query the partition's in-memory state directly — authoritative, and cheap because it's one account.

Number 3 is what most real systems do: **eventual consistency for browsing, strong consistency for the account you just transacted on.** The read model's whole value is absorbing the enormous volume of *other people's* balance checks, and you don't have to use it for the one query where freshness matters.

Worth noting what CQRS buys for this cost: projections scale independently, are rebuildable from the log when buggy, can be added retroactively, and are **fully expendable** — if every projection fails, transfers keep working and only queries degrade.

---

### 9. How would you prove the system never created or destroyed money?

The events table **is** a double-entry ledger — a `Debited` row and a `Credited` row are the two halves of one entry — which is why there's no separate `ledger` table to disagree with it. So the invariant is directly checkable:

```sql
SELECT transaction_id,
       SUM(CASE WHEN event_type = DEBITED THEN -amount_minor ELSE amount_minor END) AS net
FROM events
WHERE currency = 'USD'
GROUP BY transaction_id
HAVING net <> 0;         -- must be zero rows for every TERMINAL transaction
```

The qualification matters: during TC/C's window an in-flight transaction legitimately sums non-zero, so the check runs against **terminal** transactions at a **committed sequence position**, never as a live scan. Under that qualification a single non-zero row is created or destroyed money.

Two deeper checks event sourcing makes possible:

- **Recompute every balance from sequence 0** and compare against the `balances` projection. Any mismatch is a projection bug, with both inputs available to diagnose it.
- **Replay the log through old and new code** and diff the resulting state — a genuine regression test over production data, which is impossible without retained events.

The honest gap I'd volunteer: **nothing in this design runs those checks on a schedule with an alert.** Writing down an invariant without automating its verification is how it quietly stops being true, and that's unfinished work.

Also worth flagging as the most common way money silently goes wrong: **floating point.** `amount_minor` is a `BIGINT` of cents. `0.1 + 0.2 != 0.3`, and accumulated over a billion transactions balances drift with no single bug to point at, because the error is in the representation itself.

---

### 10. Make this multi-region.

This is the hardest unsolved piece, and I'd say so rather than sketch something that doesn't work.

**What doesn't work: a cross-region Raft group.** Consensus needs a quorum round trip, so a group spanning regions puts 100ms+ into **every** transfer, including purely local ones. That fails the latency requirement for the 99% of traffic that never crosses a region.

**The plausible shape: region-local Raft groups, cross-region transfers as ordinary two-partition TC/C.** Accounts are homed to a region; a partition's replicas all live in one region (spread across AZs). An intra-region transfer is unchanged. A cross-region transfer is TC/C where the two partitions happen to be far apart.

Three consequences, all bad in different ways:

1. **Cross-region transfer latency is inherently ~2 RTTs** — debit-first forbids parallelizing the legs (answer 2), so you pay both round trips serially. ~300ms for a transatlantic transfer, structurally.
2. **A region partition leaves transfers stuck in phase 1** with money in deficit until it heals. The recovery worker cannot resolve them — it can't reach the partition to learn whether the leg applied, and answer 3 says guessing mints money. So they sit in `UNCERTAIN`, correctly, and a user's money is genuinely unavailable for the duration.
3. **Account homing becomes a product decision with a performance cliff.** A user who moves between regions either keeps a slow home region or needs their event history migrated.

**What I'd actually do:** accept region-local groups, make cross-region transfers an explicitly slower and separately-rate-limited operation, and set the recovery deadline for cross-region legs much longer than for local ones so a brief partition doesn't strand transfers unnecessarily. And be clear with the product side that a cross-region transfer during a network partition has an indeterminate state that only resolves when connectivity returns — because the alternative is inventing money, and there's no configuration that makes that acceptable.
