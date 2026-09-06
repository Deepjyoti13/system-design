# Cache Invalidation: Purging Content From Everywhere at Once

A third worked example in this project, run through the same lens as the URL shortener and the like-counter: a plain-language overview, then HLD, LLD, DB design, and an interviewer follow-up bank.

## Why this example

The like-counter's hardest problem was thousands of writers targeting one row. This one's hardest problem is different again: a single write has to be noticed and acted on by an unknown number of independently-living copies — an app server's local memory, a shared Redis cluster, a CDN edge — each with its own propagation speed. That's the shape behind "why did the old price still show for a minute" in any system that caches at more than one layer, which is most systems past a certain size.

## How the modules fit together

| Module | Question it answers | File |
|---|---|---|
| 00 · Overview | What actually goes stale, and why does it matter? | [`00-overview.md`](./00-overview.md) |
| 01 · Architecture & HLD | What are the boxes, and why versioned keys before active purge? | [`01-architecture-hld.md`](./01-architecture-hld.md) |
| 02 · LLD | How does a repopulation race an invalidation, and who wins? | [`02-lld.md`](./02-lld.md) |
| 03 · DB Design | What does the outbox/event-log table look like? | [`03-db-design.md`](./03-db-design.md) |
| 04 · Interviewer Q&A | What would a real interviewer push on next? | [`04-interviewer-qna.md`](./04-interviewer-qna.md) |

Each of modules 01–03 ends with its own diagram, in `diagrams/`:

- **Architecture** — [`diagrams/01-architecture.svg`](diagrams/01-architecture.svg): every layer, the versioned-key path, and the active-purge fallback.
- **LLD** — [`diagrams/02-sequence.svg`](diagrams/02-sequence.svg): the exact repopulation-vs-invalidation race, and the version check that resolves it.
- **DB design** — [`diagrams/03-er.svg`](diagrams/03-er.svg): the outbox table.

## What you need going in

The same baseline as the rest of this project, plus what [Caching Strategies](../content/hld-building-blocks/caching-strategies.md), [The Transactional Outbox & CDC](../content/hld-building-blocks/transactional-outbox-cdc.md), and [Message Queues & Pub/Sub](../content/hld-building-blocks/message-queues-pubsub.md) already cover — this example is largely those three ideas applied together against one concrete problem.

## What "done" looks like

You should be able to explain, without looking back at module 01, why this design reaches for a versioned cache key *before* an active purge, and be able to point to the exact mechanism (the version-watermark compare in module 02) that stops a stale read from silently re-populating a cache the system just invalidated.
