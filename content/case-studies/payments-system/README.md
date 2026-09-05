# Design a Payments System

## Requirements

**Functional:**
- Charge a payment method (card, wallet, bank transfer) via one or more external processors.
- Support full and partial refunds against a completed payment.
- Notify merchants of payment status changes via webhook (`payment.succeeded`, `payment.failed`, `payment.refunded`).

**Non-functional** (stated as assumptions, interview-style):
- 50M transactions/day.
- p99 payment-decision latency under 2 seconds, including the external processor's own round trip.
- A payment must **never** be double-charged and must **never** be silently lost. This one requirement dominates every decision below — correctness beats speed here, explicitly, in a way it doesn't for most of this guide's other case studies.

## Capacity Estimation

Using this guide's [back-of-envelope method](../../foundations/back-of-envelope-estimation.md):

- **Transactions/sec, average:** 50M / 86,400 ≈ 580/sec. At a 5x peak factor (a flash sale, a holiday): **~2,900/sec peak**.
- **Storage/day:** assume ~500 bytes/transaction record (ids, amount, currency, status, processor reference, timestamps). 50M × 500B = 25GB/day. Transaction records are typically retained for years for compliance, not days — at ~9TB/year, a 7-year retention policy is **~65TB** of transaction history alone, before the ledger and webhook logs.
- **Webhook deliveries:** roughly 1-2 per transaction (succeeded, and later maybe refunded) → **~600-1,200/sec average**.

## Approach Walkthrough

Before any boxes: the system records its **intent** to charge — durably, locally — *before* it ever calls an external processor, then calls the processor, then records the outcome, then notifies the merchant. That ordering is the whole design. Calling the processor first and only recording afterward would leave a crash-mid-call in an unrecoverable, ambiguous state: did the charge go through or not? Recording intent first means a crash always leaves behind a concrete `pending` row that a recovery job can resolve later — never a question mark.

## API Surface

- `POST /payments {idempotency_key, amount, currency, payment_method, merchant_id}` → `{payment_id, status}`. `idempotency_key` is **required**, not optional (cross-ref [Idempotency Keys](../../scalability-resilience/idempotency-keys.md)) — there is no safe version of this endpoint without it.
- `POST /payments/{id}/refund {idempotency_key, amount?}` → `{refund_id, status}` — omitting `amount` refunds the full remaining balance.
- `GET /payments/{id}` → current status, for polling.
- Outbound webhook to the merchant: `payment.succeeded {payment_id, amount, merchant_id}`, `payment.failed {payment_id, reason}`, `payment.refunded {payment_id, refund_id, amount}`.

## High-Level Design

![Payment flow: atomic intent recording, the external processor call, and the outbox-relayed webhook, with the crash-recovery point marked](diagrams/hld.svg)

**Building blocks:**
- **Payment Orchestration Service** — on a charge request, writes a `payment_intents` row (`status = pending`) in the *same local transaction* as accepting the request. This is the atomic step the whole design depends on, and it only ever needs to span one database, which is why it's safe.
- **External processor call** — a bounded-timeout call to the actual card network/processor, with retries governed by [Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md). Retries here are non-negotiably idempotent: the same `idempotency_key` is forwarded to the processor itself (real processors support this), so a retried call after a timeout can never result in two real-world charges.
- **Outbox + webhook relay** — the payment's outcome and the merchant webhook are written to an `webhooks_outbox` table in the same transaction as the status update, then relayed asynchronously (cross-ref [The Transactional Outbox & CDC](../../hld-building-blocks/transactional-outbox-cdc.md)) — so a merchant is reliably notified even if the direct API response to the original request was lost in transit.
- **Ledger Service** — every settled transaction writes a **double-entry** pair: a debit row and a credit row, never a single balance update. The ledger is self-auditing this way — sum every entry across the system, and a nonzero total means something is wrong, immediately, without needing to trust any one balance column.

**Load Handling.** A flash-sale peak is exactly the load spike this design has to absorb without shedding the payment path itself — unlike this guide's other systems, you cannot drop a payment request the way [Backpressure, Load Shedding & Bulkheads](../../scalability-resilience/backpressure-load-shedding.md) recommends shedding a "recommendations" call. What *can* be shed under pressure: deferring non-critical work riding alongside the charge (fraud-score enrichment, analytics events) — never the intent write or the processor call. The Orchestration Service and processor-call tier both scale horizontally and statelessly to absorb the multiple; a concrete load-test target: sustain 3,000 requests/sec for 10 minutes with p99 decision latency still under 2 seconds.

**Concurrent-User Handling.** Two races, named explicitly:
1. **The same `idempotency_key` submitted twice concurrently** (a client retry racing its own original request) — resolved exactly as [Idempotency Keys](../../scalability-resilience/idempotency-keys.md) describes: a unique constraint on `payment_intents(idempotency_key)` means the second concurrent insert fails the constraint, not a race the application code has to detect itself; the loser's request handler catches that failure and returns the *first* attempt's result instead of erroring.
2. **A refund request racing a not-yet-confirmed charge** — a refund can only be issued against a payment whose status is already `succeeded`; if the intent is still `pending` or `processing`, the refund request is rejected outright ("payment not yet settled") rather than queued against an outcome that doesn't exist yet — there is no safe way to refund money that hasn't definitively moved.

## Low-Level Design

![Payment state machine, and the idempotency-key-collision sequence for two concurrent identical requests](diagrams/lld.svg)

**`PaymentStatus`**, as an explicit state machine, matching this guide's convention elsewhere: `pending → processing → succeeded | failed`, with `succeeded → refunded` as a separate, one-way transition.

Pseudocode for the charge flow:
```
OrchestrationService.charge(idempotency_key, amount, currency, payment_method, merchant_id):
    existing = PaymentIntents.findByIdempotencyKey(idempotency_key)
    if existing is not None:
        return existing                                  # duplicate request, already handled

    intent = PaymentIntents.insert(idempotency_key, amount, currency, status="pending")
                                                           # ^ same local transaction as above
    result = ProcessorClient.charge(intent.id, amount, currency, payment_method,
                                     idempotency_key=idempotency_key)   # forwarded, not regenerated

    with db.transaction():
        intent.status = "succeeded" if result.ok else "failed"
        PaymentIntents.update(intent)
        Outbox.insert(event="payment." + intent.status, payload=intent)   # atomic with the status update

    return intent
```

**Reconciliation job**, for the case the pseudocode above can't resolve on its own: a `payment_intent` stuck in `processing` because the processor call timed out with no response ever arriving. Neither assuming success nor assuming failure is safe here — the job instead queries the *processor's own status API* for that transaction (a safe, idempotent read) to learn the true outcome, then applies the same status-update-plus-outbox-write step the main flow uses.

## Database Design & Scaling

![Schema: payment_intents, double-entry ledger_entries, and the webhooks_outbox](diagrams/er.svg)

**Entities:** `payment_intents` (id, idempotency_key UNIQUE, merchant_id, amount, currency, status, processor_ref, created_at, updated_at), `ledger_entries` (id, payment_id, account_id, entry_type: debit|credit, amount, created_at — one payment produces exactly two rows), `webhooks_outbox` (id, payment_id, event_type, payload, sent_at).

**The unique constraint on `idempotency_key` is the actual concurrency-safety mechanism**, not a nice-to-have layered on top — it's what makes the "duplicate request" case in the Concurrent-User Handling section a guaranteed, database-enforced outcome rather than an application-level check that could race.

**Why strongly consistent and relational, not eventually consistent:** money is the canonical case both [ACID vs BASE](../../database-design/acid-vs-base.md) and [SQL vs NoSQL](../../database-design/sql-vs-nosql.md) already name for exactly this choice — a balance that's briefly wrong is not an acceptable trade the way a briefly-stale like-count is.

**Sharding**, once volume demands it: by `merchant_id` (or the payer's `account_id` for the ledger specifically), keeping one account's full ledger on one shard so a balance query never has to fan out and merge across shards to answer "what does this account's history actually show right now."

## Interviewer Q&A

**What happens when two requests hit the same resource at the same instant — specifically, the same idempotency key submitted twice concurrently?**
The unique constraint on `payment_intents(idempotency_key)` means only one insert can win; the second concurrent request's insert fails the constraint and its handler returns the *first* request's already-in-flight or completed result, never attempting a second charge.

**What happens when traffic spikes 10x for an hour (a major sale event)?**
The Orchestration Service and processor-call tier scale horizontally since both are stateless per request; the harder constraint is the external processor's own rate limit, which this system can't scale around — request queuing with the same bounded-timeout-and-retry discipline as the normal path absorbs the burst, and nothing on the payment write path is shed, per the Load Handling section above.

**What if the processor actually succeeded, but the response was lost before your service saw it — doesn't a retry now double-charge?**
No, for two independent reasons: the forwarded idempotency key means the processor itself recognizes the retry and returns the original charge's result rather than charging again, and even without that, the reconciliation job would eventually query the processor's status API and discover the true "succeeded" outcome rather than assume failure and retry blindly.

**How do refunds interact with the ledger — do you modify the original entries?**
Never — a refund writes a *new* pair of ledger entries reversing the original debit/credit, rather than modifying or deleting the original rows. The ledger is an append-only audit trail; "what actually happened" always has to be reconstructable from the full sequence of entries, not just the current state.

**Would you ever accept eventual consistency here, the way this guide's [Counting a Billion Likes](../../../like-counting-at-scale/00-overview.md) case study does for a like count?**
No — a like count being briefly wrong is invisible and harmless; an account balance being briefly wrong is either a real financial loss or a real overcharge, depending on which direction it's wrong. This is exactly the CP-leaning choice [Latency, Throughput & the CAP Theorem](../../foundations/latency-throughput-cap.md) contrasts against an AP-leaning one.

**What's the failure mode if the webhook relay itself goes down?**
The merchant's notification is delayed, not lost — the payment's own status is already durably committed before the outbox write even happens, and the outbox row sits there until the relay is back up. A merchant polling `GET /payments/{id}` in the meantime would already see the correct, final status.

**Why record intent locally before calling the processor, instead of calling the processor first and recording the result?**
Because "call first, record after" leaves a crash mid-call in a state with *nothing durable to recover from* — you don't know if the charge happened. "Record intent first" always leaves a concrete `pending` row the reconciliation job can resolve, turning an unknown into a recoverable, well-defined state.

**How would you test this without hitting a real payment processor on every test run?**
A sandbox/mock implementation of the `ProcessorClient` interface, exercised with contract tests that assert the mock's response shape matches the real processor's documented API — the Orchestration Service itself never needs to know which one it's talking to, the same interface-over-implementation pattern this guide uses for its other pluggable dependencies.

**Would you shard `payment_intents` and `ledger_entries` by the same key as a typical high-write case study in this guide, like sharding by a UUID hash?**
No — an arbitrary hash spread would scatter one account's ledger across every shard, making "what's this account's balance right now" a fan-out-and-merge query instead of a single-shard lookup. `merchant_id`/`account_id` is the key that matches the query that actually runs constantly, the same reasoning [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) gives for choosing a shard key from the access pattern, not from a desire for even distribution alone.
