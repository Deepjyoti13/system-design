# Deploying Without Dropping a Single Request

A fourth worked example in this project, run through the same modular lens as the URL shortener and the other Real-World Deep Dives — with one twist: this is the first topic in this project where the "database" (module 03) isn't user-facing data at all, but the deploy system's own state.

## Why this example

Every other case study in this project asks "how does this system serve users correctly at scale." This one asks a different question: "how do you change the system underneath users without them ever noticing" — a genuinely different interview shape, closer to infrastructure/platform engineering than to product-feature design, and one that tests whether a candidate can reason about a system's own operational lifecycle, not just its steady-state behavior.

## How the modules fit together

| Module | Question it answers | File |
|---|---|---|
| 00 · Overview | What does "zero-downtime" actually mean, precisely? | [`00-overview.md`](./00-overview.md) |
| 01 · Architecture & HLD | What are the boxes, and why a control-plane/data-plane split? | [`01-architecture-hld.md`](./01-architecture-hld.md) |
| 02 · LLD | What's the instance lifecycle state machine, and who's allowed to drive it? | [`02-lld.md`](./02-lld.md) |
| 03 · DB Design | What state does a deploy system need to persist, when there's no "user data" at all? | [`03-db-design.md`](./03-db-design.md) |
| 04 · Interviewer Q&A | What would a real interviewer push on next? | [`04-interviewer-qna.md`](./04-interviewer-qna.md) |

Each of modules 00–03 ends with its own diagram, built locally with the excalidraw-diagram skill:

- **Overview / Architecture** — [`diagrams/01-architecture.svg`](./diagrams/01-architecture.svg): the control-plane/data-plane split, the rolling-batch path, and the WebSocket reconnect-first path.
- **LLD** — [`diagrams/02-sequence.svg`](./diagrams/02-sequence.svg): the instance lifecycle state machine and one batch's call sequence.
- **DB design** — [`diagrams/03-er.svg`](./diagrams/03-er.svg): the rollout state model and why it lives in a consensus-backed store.

## What you need going in

The same baseline as the rest of this project — client/server, HTTP — plus [Load Balancing](../content/hld-building-blocks/load-balancing.md)'s health-check framing and [Replication & Consensus](../content/hld-building-blocks/replication-consensus.md)'s point about exactly-one-writer correctness, both of which this example leans on directly rather than re-deriving.

## What "done" looks like

You should be able to answer, without looking back at module 01, *why a batch's capacity dip is bounded and by what* — and be able to point to the exact mechanism (the drain window) that turns "an instance is being replaced" into "zero requests are dropped" rather than "very few requests are dropped." Module 04's ten questions are the real test: if a question there surprises you, the module it traces back to is where to go re-read.
