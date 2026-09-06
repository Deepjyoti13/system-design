# Module 00 — Feature Overview

## The feature, with no infrastructure in it yet

A user taps "Pay $240." Somewhere between that tap and the "Payment approved" screen, the system has to decide — silently, almost always in the user's favor — whether this specific charge looks like fraud. Nearly every legitimate transaction sails through with the decision completely invisible; a small minority get declined outright, and a smaller minority still get routed to a slower manual check ("we need to verify this purchase").

Two things about that description matter enormously for everything that follows, even though neither is visible to the user:

- **The decision has to happen inside the payment's own latency budget.** This isn't a fraud team reviewing a report the next morning — by the time a batch job would flag a stolen card, the money is already gone. The check rides *inside* the same request this guide's [Payments System](../content/case-studies/payments-system/00-overview.md) case study already designs, not after it.
- **A wrong answer in either direction has a real cost.** Decline a legitimate purchase and you've lost a sale (and annoyed a real customer); approve a fraudulent one and you've lost the money outright. Unlike most of this guide's systems, there's no "safe" default to fall back on — both failure directions are expensive, which is exactly why this gets its own module instead of being a footnote on the payments case study.

That tension — a real-time decision, on the critical path, where being wrong is expensive either way — is the entire reason this is an interesting design problem instead of a single `if` statement. Module 01 picks it up from there.

## What this module deliberately leaves out

No feature store, no scoring service, no risk tiers are named yet. If you can't describe the feature in plain language first, you'll end up designing infrastructure to solve a problem you haven't actually stated. Every box that shows up starting in module 01 has to trace back to making *this* — a fast, mostly-invisible, occasionally-wrong-but-rarely decision — hold up at payment volume.
