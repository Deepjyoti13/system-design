# Push Notifications: One Event, Millions of Phones

A worked system-design example, carried through overview → HLD → LLD → DB design → interviewer follow-ups, the same way this project's URL-shortener example is — for a fan-out problem instead of a lookup problem.

## Why this example

A URL shortener is a read-heavy point-lookup problem. This one is the opposite shape: a single event (a celebrity's post) has to become millions of independent, provider-specific deliveries within seconds, without one slow provider or one huge fan-out starving an unrelated 2FA code that needs to arrive in under 2 seconds. That's a different set of muscles — fan-out partitioning, per-traffic-class isolation, distributed dedup, at-least-once delivery — worth designing explicitly rather than assuming "send a push" is trivial.

## How the modules fit together

| Module | Question it answers | File |
|---|---|---|
| 00 · Overview | What does the user actually experience? | [`00-overview.md`](./00-overview.md) |
| 01 · HLD | What are the boxes, and how does an event travel through them under load? | [`01-architecture-hld.md`](./01-architecture-hld.md) |
| 02 · LLD | What are the classes inside the dispatch worker, and how does the dedup race actually get resolved in code? | [`02-lld.md`](./02-lld.md) |
| 03 · DB design | What does device-token and delivery data actually look like on disk? | [`03-db-design.md`](./03-db-design.md) |
| 04 · Interviewer Q&A | What would a real interviewer push on, and what's the answer? | [`04-interviewer-qna.md`](./04-interviewer-qna.md) |

Work through 00 → 01 → 02 → 03 in order — each hands a concrete decision to the next (HLD decides transactional and bulk get separate queues; LLD decides the dedup claim has to be distributed, not an in-process lock; DB design decides why the token store and the delivery log can't be the same database). Module 04 is the test: can you answer each question *from* the design, not from memory.

## What you need going in

The same baseline as the rest of this project: client/server, HTTP, and now also a basic notion of a message queue (what "at-least-once delivery" means). Nothing here assumes a specific cloud provider.

## What "done" looks like

You can look at the four diagrams together and explain, out loud, why a 2FA code and a celebrity's post never compete for the same resource anywhere in this design — and trace that answer back to a single decision in module 01 (separate topics, separate worker pools) rather than a vague "priority" hand-wave.
