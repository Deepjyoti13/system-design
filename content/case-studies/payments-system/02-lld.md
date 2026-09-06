# Module 02 — Low-Level Design

![Payment state machine, and the idempotency-key-collision sequence for two concurrent identical requests](diagrams/lld.svg)

**`PaymentStatus`**, as an explicit state machine, matching this guide's convention elsewhere: `pending → processing → succeeded | failed`, with `succeeded → refunded` as a separate, one-way transition. Modeling this as a named enum with enforced transitions — not a free-text `status` string — is what makes "a refund can only follow a success" and "a terminal state never reopens" checkable at the type level instead of relying on every caller remembering the rule.

## Interfaces vs. implementations

- **`PaymentIntentRepository`** *(interface)* → **`SqlPaymentIntentRepository`** — `findByIdempotencyKey(key)`, `insert(...)`, `updateStatus(id, from, to)` where the update is conditional on the *current* status matching `from` (the enforcement point for one-way transitions).
- **`ProcessorClient`** *(interface)* → **`StripeProcessorClient`** / **`AdyenProcessorClient`** — `charge(amount, currency, method, idempotencyKey)`, `refund(paymentId, amount, idempotencyKey)`, `getStatus(processorRef)` (used by the reconciliation worker). Every real processor becomes one implementation behind this single interface.
- **`LedgerWriter`** *(interface)* → **`SqlLedgerWriter`** — `recordPair(paymentId, debitAccount, creditAccount, amount)`, always writing exactly two rows, never a single balance mutation.
- **`OutboxWriter`** *(interface)* → **`SqlOutboxWriter`** — `enqueue(eventType, payload)`, called inside the same transaction as a status update.
- **`OrchestrationService`** — the orchestrator. Depends on all four interfaces, implements none of the storage or network calls itself.

## Pseudocode for the charge flow

```
OrchestrationService.charge(idempotency_key, amount, currency, payment_method, merchant_id):
    existing = intentRepo.findByIdempotencyKey(idempotency_key)
    if existing is not None:
        return existing                                  # duplicate request, already handled

    intent = intentRepo.insert(idempotency_key, amount, currency, status="pending")
                                                           # ^ same local transaction as above

    result = processorClient.charge(amount, currency, payment_method,
                                     idempotencyKey=idempotency_key)   # forwarded, not regenerated

    with db.transaction():
        newStatus = "succeeded" if result.ok else "failed"
        applied = intentRepo.updateStatus(intent.id, from="pending", to=newStatus)
        if applied:
            outboxWriter.enqueue("payment." + newStatus, intent)
            if newStatus == "succeeded":
                ledgerWriter.recordPair(intent.id, merchant_account, customer_account, amount)

    return intent
```

The `updateStatus(..., from="pending", ...)` conditional write is the same discipline this guide's [distributed job scheduler](../distributed-job-scheduler/01-architecture-hld.md) uses for its atomic claim: the transition only applies if the row is still in the expected prior state, which is what makes the reconciliation worker (below) safe to run concurrently with the main flow instead of needing a separate lock.

## Error cases worth designing for deliberately

- **Duplicate request (idempotency-key collision):** `findByIdempotencyKey` returning a hit is not an error — it's the correct, common case for a client retry. Returning the existing intent rather than re-attempting the charge is what makes the endpoint safe to call more than once.
- **Processor call times out with no response:** this is genuinely ambiguous — the charge may have succeeded on the processor's side with the confirmation lost in transit. The intent stays `pending`, and this is precisely the state the reconciliation worker exists to resolve, rather than the request handler guessing.

**Reconciliation job**, for the case the pseudocode above can't resolve on its own: a `payment_intent` stuck in `processing` because the processor call timed out with no response ever arriving. Neither assuming success nor assuming failure is safe here — the job instead queries the *processor's own status API* for that transaction (a safe, idempotent read) to learn the true outcome, then applies the same `updateStatus`-plus-outbox-write step the main flow uses.

## Concurrency at the code level

`intentRepo.updateStatus(id, from, to)` needs no in-process lock, and this is worth stating explicitly: the Orchestration Service runs on many horizontally-scaled instances, so a language-level mutex would only protect against other threads *on the same instance* — it would do nothing about another instance's reconciliation worker updating the same row a moment later. Correctness comes entirely from the conditional update being enforced by the database itself (`UPDATE ... WHERE status = ?`), the same pattern this guide uses everywhere two writers might race for the same row: push the atomicity requirement down into the one system that can actually provide it for free.

The one place an actual application-level guard *is* needed: the read-then-decide step in `charge()` (`findByIdempotencyKey` followed by `insert`) is not itself atomic in application code — two concurrent identical requests can both pass the `existing is None` check before either has inserted. This is exactly why the safety net is the database's unique constraint on `idempotency_key`, not the `if` statement: the `if` is an optimization that avoids unnecessary processor calls in the common case, while the constraint is what actually guarantees correctness in the race case.

## Design patterns you just used, named

- **Repository pattern** — `PaymentIntentRepository` and `LedgerWriter` hide storage behind method calls; `OrchestrationService` never issues SQL directly.
- **Strategy pattern** — `ProcessorClient` is a strategy: `StripeProcessorClient` and `AdyenProcessorClient` are interchangeable behind one interface, and routing between them (multi-processor failover, noted in Architecture & HLD) is a strategy-selection decision, not a rewrite of the orchestrator.
- **State pattern (via an explicit enum, not a class hierarchy)** — `PaymentStatus`'s enforced one-way transitions are the state-machine discipline this guide applies consistently: model a lifecycle as named states with legal transitions, never as a boolean or a free-text field.
- **Transactional outbox** — `OutboxWriter` is this pattern by name: durably queuing an event in the same transaction as the state change it describes, so the event can never be "sent" without the state change actually having committed, or vice versa.

## Practice: extend it yourself

Before moving to Database Design, sketch (pseudocode is fine) how you'd add:

1. **Partial refunds, more than once against the same payment** — a $100 payment refunded $30, then later $20. Which component tracks "how much has already been refunded," and what does the ledger look like after both refunds — two reversing pairs, or one running adjustment?
2. **A second processor as automatic failover** — if `StripeProcessorClient.charge()` fails its circuit breaker, retry the same logical charge against `AdyenProcessorClient`. Does the `idempotency_key` forwarded to Adyen need to be different from the one sent to Stripe, and why might that matter if the *original* Stripe call actually went through moments after the breaker gave up on it?

Neither has one clean answer — the point is noticing that the interfaces already drawn (`ProcessorClient`, `LedgerWriter`) make it obvious which component *should* own each new piece of behavior, even before you've fully worked out what that behavior is.
