# Module 04 — Interviewer Q&A

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
