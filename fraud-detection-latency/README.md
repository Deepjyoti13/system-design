# Catching Fraud in the Time It Takes to Approve a Payment

A real-time risk decision riding *inside* the payment path this guide's [Payments System](../content/case-studies/payments-system/00-overview.md) case study already designs, run through the same module shape as the rest of this project's deep dives.

## Why this example

This guide's payments case study covers crash-safety and correctness for money that's already been decided to move. This module covers the decision itself: is this specific charge legitimate, made in a fraction of the payment's own latency budget, where being wrong in either direction — a false decline or a missed fraud case — has a real cost. That's a distinct interview shape from "make a write durable": it's "make a decision, fast, using data that has to already be warm when the request arrives."

## How the modules fit together

| Module | Question it answers | File |
|---|---|---|
| 00 · Overview | What does the decision actually look like, with no infrastructure in it yet? | [`00-overview.md`](./00-overview.md) |
| 01 · Architecture & HLD | What are the boxes, and why a warm feature store instead of a live query? | [`01-architecture-hld.md`](./01-architecture-hld.md) |
| 02 · LLD | Where, exactly, is the atomic operation that makes the velocity check race-safe? | [`02-lld.md`](./02-lld.md) |
| 03 · DB Design | Why does the hot counter live somewhere completely different from the audit trail? | [`03-db-design.md`](./03-db-design.md) |
| 04 · Interviewer Q&A | What would a real interviewer push on next? | [`04-interviewer-qna.md`](./04-interviewer-qna.md) |

Each of modules 01–03 ends with its own diagram.

## What you need going in

This guide's [Payments System](../content/case-studies/payments-system/00-overview.md) case study (the payment path this check rides inside of), [Rate Limiting](../content/hld-building-blocks/rate-limiting.md) (the distributed-counter mechanism this module reapplies to velocity instead of quota), and [Circuit Breakers & Retries](../content/scalability-resilience/circuit-breakers-retries.md) (the fail-open fallback pattern).

## What "done" looks like

You should be able to explain, without looking back at module 01, *why the velocity counter is never a relational-table row count* — and name the exact atomic operation (module 02's `incrementAndGet`) and the exact reason it lives in a different store than the audit log (module 03). Module 04's questions are the real test: the fail-open-vs-fail-closed question in particular is the one this whole module exists to make you able to answer with a specific mechanism, not a vague instinct.
