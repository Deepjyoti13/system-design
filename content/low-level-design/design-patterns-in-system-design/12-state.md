# State

![State: PaymentStatus as an enforced state machine, every arrow a legal transition, everything else a rejected write](diagrams/state.svg)

## The Problem It Solves

An object's valid operations depend entirely on which phase of its lifecycle it's currently in, and modeling that with a free-text status field or a handful of booleans lets an invalid transition (or the same transition applied twice) slip through silently — nothing stops `refunded` from being written before `succeeded` ever landed, or the same transition being applied by two racing writers.

## How It Works

The lifecycle is modeled as a named, closed set of states, with only certain transitions between them declared legal. Critically, the transition itself is enforced as a *conditional* operation at the point of write — not just checked in application code beforehand — so that an out-of-order or duplicated write becomes a no-op rather than a silent corruption, even under concurrent writers.

## Implementation

```
enum PaymentStatus: pending, processing, succeeded, failed, refunded

# the transition is enforced as a conditional write, not an unconditional overwrite:
applied = paymentRepo.updateStatus(paymentId, from="processing", to="succeeded")
if not applied:
    # the row wasn't in "processing" -- this write is a no-op, not a silent corruption
    return
```

## Real-World Use Case

This guide's [Payments System's `PaymentStatus`](../../case-studies/payments-system/02-lld.md) is the canonical example already built out in depth: `pending → processing → succeeded | failed`, with `succeeded → refunded` as a separate, one-way transition. The payoff isn't the enum by itself — it's enforcing the transition *at the point of write* (`WHERE status = 'processing'`), so "refunded before succeeded" or the same transition applied twice by a retried request is a zero-row update, not a race the application has to detect after the fact.

## When to Use It

- An object has a genuine lifecycle with more than two meaningfully different phases.
- Some operations are only valid in certain phases, and performing them in the wrong phase would be a real bug, not a cosmetic issue.
- Multiple writers (retries, concurrent requests, a reconciliation job) might attempt the same transition, and the *last* or *duplicate* write needs to be a safe no-op rather than data corruption.

## When NOT to Use It

An object with only two states and one transition between them doesn't need a named state machine — a single boolean says everything a `State` enum would, with less ceremony.

## Related Patterns

State is often confused with [Strategy](10-strategy.md) because both swap behavior behind an interface — the difference is that State's "which behavior is active" changes *based on the object's own history* (a payment can't go back to `pending` once it's `succeeded`), while Strategy's active implementation is chosen independently of any prior state and can be swapped freely at any time.
