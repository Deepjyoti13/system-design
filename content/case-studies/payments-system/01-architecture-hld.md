# Module 01 — Architecture & High-Level Design

![Payment flow: atomic intent recording, the external processor call, and the outbox-relayed webhook, with the crash-recovery point marked](diagrams/hld.svg)

## Monolith vs. microservices

Payments is pulled out as its own service, never folded into a general Orders or Checkout monolith, for a reason that's regulatory as much as technical: PCI-DSS scope. Any service that touches raw card data has to meet a strict, audited compliance bar — encryption at rest, restricted network access, dedicated audit logging — and folding payment handling into a monolith would drag the *entire monolith* into that same compliance scope, including code that has nothing to do with money. Isolating Payments means only this one service (and its data store) needs to pass a PCI audit; Orders, Catalog, and everything else stay outside that boundary entirely.

There's a second, independent reason the seam holds even ignoring compliance: Payments has a fundamentally different reliability contract than the rest of a commerce platform. A recommendations service or a search service can degrade or return stale data under load — a payments service cannot silently drop a request, ever (see Load Handling below). Provisioning an entire monolith to the availability and correctness bar payments actually needs would be paying that cost everywhere for a guarantee only one code path requires. If your system is small enough that no external audit has ever asked "where does card data flow," this split isn't buying you anything yet — say so rather than defaulting to microservices because it looks more serious.

## Building blocks

| Block | Role |
|---|---|
| **Payment Orchestration Service** (stateless) | On a charge request, writes a `payment_intents` row (`status = pending`) in the *same local transaction* as accepting the request — the one atomic step the whole design depends on |
| **External Processor Client** | Bounded-timeout call to the actual card network/processor, idempotent by forwarding the same `idempotency_key` the processor itself understands |
| **Outbox + Webhook Relay** | Writes the payment's outcome and the merchant webhook event to a `webhooks_outbox` table in the same transaction as the status update, then relays asynchronously |
| **Ledger Service** | Every settled transaction writes a double-entry pair (one debit row, one credit row) — never a single balance update |
| **Reconciliation Worker** | Periodically resolves `payment_intents` stuck in `processing` by querying the processor's own status API |

## Per-path walkthrough

**Charge path (write)** — `Client → LB → Orchestration Service (write payment_intents, status=pending, SAME transaction as accepting the request) → Processor Client (bounded-timeout call, forwarded idempotency_key) → Orchestration Service (write final status + outbox row, SAME transaction) → Client response`. The two "same transaction" points are the entire design: everything durable that has to be atomic is atomic *within one database*, and the one step that can't be (the network call to the processor) is bracketed by two states — `pending` before, `succeeded`/`failed` after — that a crash can never leave ambiguous.

**Refund path** — `Client → LB → Orchestration Service (validate status == succeeded) → Processor Client (refund call) → Ledger Service (reversing debit/credit pair) → Outbox (payment.refunded)`. Structurally identical to the charge path — same atomicity discipline, same idempotency-key requirement — which is itself worth noticing: a refund isn't a special case bolted on afterward, it's the same state-machine-plus-outbox pattern applied to a different transition.

**Async path — webhook delivery** — `webhooks_outbox → Relay (polls or CDC-tails the outbox) → Merchant HTTPS endpoint (with retry + backoff)`. Decoupled entirely from the charge path's response — a merchant's webhook endpoint being slow or down for an hour has zero effect on whether the charge itself succeeds, only on how promptly they hear about it.

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Crash-safety mechanism | Record intent locally, then call processor | Call processor first, record result after | "Call first" leaves a crash mid-call with nothing durable to recover from; "record first" always leaves a concrete `pending` row a reconciliation job can resolve |
| Cross-service atomicity | Local transaction + transactional outbox | Two-phase commit (2PC) across the DB and the processor | 2PC needs the processor to participate in your commit protocol — no real card processor offers this; the [outbox pattern](../../hld-building-blocks/transactional-outbox-cdc.md) gets atomicity by only ever committing atomically against your *own* database |
| Merchant notification | Async webhook via outbox relay | Synchronous callback during the charge request | The merchant's endpoint being slow must never add latency to — or block — the charge decision itself; decoupling means their downtime is invisible to the payment path |
| Balance representation | Double-entry ledger (debit + credit rows, append-only) | Single mutable `balance` column, updated in place | A mutable balance can silently drift from reality with no audit trail of how; double-entry is self-auditing — sum every entry, and a nonzero total means something is provably wrong |
| Refund modeling | New reversing ledger entries | Mutate/delete the original entries | The ledger must reconstruct "what actually happened" from history alone; deleting or editing rows destroys the audit trail this system exists to keep intact |

## Load Handling

- **Peak-vs-average tolerance:** the 5x peak factor from Capacity Estimation (~2,900/sec) is an ordinary horizontal-scaling problem for the stateless Orchestration Service and Processor Client tiers — more instances, same logic. A flash sale is exactly this kind of load spike, and it's the one this design is built to absorb without shedding the payment path itself.
- **Where backpressure kicks in first:** unlike this guide's other systems, you cannot drop a payment request the way [Backpressure, Load Shedding & Bulkheads](../../scalability-resilience/backpressure-load-shedding.md) recommends shedding a "recommendations" call under pressure — a shed payment is a lost sale or an inconsistent state, not a degraded experience. What *can* be shed: non-critical work riding alongside the charge (fraud-score enrichment, analytics events, receipt-email generation) — never the intent write or the processor call.
- **What gets shed under overload:** nothing on the critical path. If the system is genuinely overwhelmed, requests queue behind the Orchestration Service's own bounded concurrency limit and return a `503` with `Retry-After` rather than accepting a request the design can't safely honor — refusing to accept is always safer here than accepting and silently degrading a money-moving guarantee.
- **The processor's own rate limit, not your infrastructure, is usually the real ceiling.** Even with unlimited app-server capacity, most real processors cap requests/sec per merchant account — a constraint this design can't scale around by adding more servers. Request queuing with the same bounded-timeout-and-retry discipline as the normal path is what absorbs a burst against that external ceiling.
- **Load-test target:** sustain 3,000 requests/sec for 10 minutes with p99 decision latency still under 2 seconds, zero double-charges, and zero requests silently dropped (every request either succeeds, fails cleanly, or is durably `pending` for the reconciliation worker to resolve).

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| Same `idempotency_key` submitted twice concurrently (client retry racing its own original request) | Unique constraint on `payment_intents(idempotency_key)` — the second concurrent insert fails the constraint at the database level, not an application-level check that could itself race | The first attempt's result, not an error — the handler catches the constraint violation and returns the already-in-flight or completed payment |
| Refund request racing a not-yet-confirmed charge | Refunds are only accepted against `status == succeeded`; a `pending`/`processing` intent rejects the refund outright | An explicit "payment not yet settled" error — never a queued refund against an outcome that doesn't exist yet |
| Two refund requests for the same payment, submitted concurrently (double-tap on a merchant's dashboard) | Same idempotency-key discipline as the charge path — the refund endpoint requires its own `idempotency_key`, uniquely constrained the same way | The first refund's result; the second is a no-op returning the same `refund_id`, never a second reversing ledger pair |
| Reconciliation worker resolves an intent at the exact moment the original request's processor response finally arrives | Whichever write reaches the database first wins the status transition; the state machine's transitions are enforced as one-way (see Database Design) so the second writer's update is a no-op against an already-`succeeded`/`failed` row | The losing writer's update silently no-ops rather than double-applying — nothing downstream (ledger, outbox) fires twice, because both writes are themselves idempotent against the same target status |

## Scaling & Reliability

- **Horizontal scaling:** Orchestration Service and Processor Client are both stateless and scale behind the load balancer by request rate, same as any stateless tier in this guide.
- **Circuit breaker:** the processor call is wrapped in a circuit breaker (cross-ref [Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md)) — if a specific processor starts timing out systematically, the breaker trips and the system can fail over to a secondary processor (if configured) or fail the request cleanly rather than piling up threads waiting on a processor that's already down.
- **Retries:** only ever idempotent, and only ever with the *same* forwarded `idempotency_key` — a retry that generated a new key would defeat the entire point and risk a real duplicate charge. Bounded to a small number of attempts with exponential backoff, since an unresolved request converts to a durable `pending` row the reconciliation worker will pick up regardless.
- **Dead-letter queue:** a webhook that fails delivery after N retries lands in a DLQ rather than being dropped or retried forever — a merchant's endpoint being down for days shouldn't consume the relay's capacity retrying it against every other merchant's traffic.
- **Graceful degradation:** if the Ledger Service or webhook relay is unavailable, the *charge itself* still completes and its status is durably correct — only the audit-ledger write or the merchant notification lags, both of which are designed to catch up from where the outage left off (the ledger write is part of the same transaction as the status update in the common case, but a Ledger Service outage during that transaction fails the whole write cleanly, converting to a `pending`-then-reconciled path rather than a partial, inconsistent commit).
- **Multi-region:** not built here, and worth naming as a real gap rather than glossing over it — see "what you'd revisit" below.

## What you'd revisit as this grows

- **Multi-processor failover.** This design assumes one processor per request; a mature system routes to a secondary processor automatically when the primary's circuit breaker is open, which means the `idempotency_key` discipline has to extend across processors, not just within one.
- **Multi-region active-active.** A single-region design is a single point of regional failure for a system whose entire premise is "never silently fail." Real payment platforms run active-active across regions with careful conflict resolution on the ledger — a much harder problem than this design takes on, and worth naming as future work rather than pretending it's solved.
- **Reconciliation at scale.** The reconciliation worker's periodic scan for stuck `processing` rows gets more expensive as transaction volume grows; a mature version would index directly on `(status, updated_at)` and use CDC to react to timeouts rather than polling.
- **Fraud detection sitting in front of the orchestration path**, without adding synchronous latency to it — deliberately scoped out of this module so it can be reasoned about on its own; it's a separate design problem (a real-time scoring service in the critical path with its own latency budget) layered on top of, not tangled into, the crash-safety discipline this module covers.
