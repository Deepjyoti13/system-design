# Counting a Billion Likes Without a Billion Row Locks

A second worked example in this project, run through the same three-level lens as the URL shortener (`../01-hld-fundamentals.md` etc.), plus two layers that worked example doesn't have: a plain-language feature overview before the architecture, and an interviewer follow-up bank after the schema.

## Why this example

The URL shortener's hardest problem was a cache in front of a database. This one's hardest problem is different in kind: what happens when *thousands of writers per second* target the exact same row. That's a distinct, common interview shape — anything with a hot aggregate (view counts, upvotes, leaderboard scores) — and it doesn't yield to "add a cache," because the write, not the read, is what's contended.

## How the modules fit together

| Module | Question it answers | File |
|---|---|---|
| 00 · Overview | What does the user actually see? | [`00-overview.md`](./00-overview.md) |
| 01 · Architecture & HLD | What are the boxes, and why a queue here / an atomic op there? | [`01-architecture-hld.md`](./01-architecture-hld.md) |
| 02 · LLD | What are the interfaces inside the Like Service, and where — if anywhere — is a lock needed? | [`02-lld.md`](./02-lld.md) |
| 03 · DB Design | What does the schema look like, and who's allowed to write the denormalized count? | [`03-db-design.md`](./03-db-design.md) |
| 04 · Interviewer Q&A | What would a real interviewer push on next? | [`04-interviewer-qna.md`](./04-interviewer-qna.md) |

Each of modules 00–03 ends with its own diagram, published as its own page:

- **Overview** — [what the user sees](https://claude.ai/code/artifact/aab1ad83-46a9-4fc6-be9f-61b6167eee0e): the toggle, with zero infrastructure visible.
- **Architecture** — [write once, count everywhere](https://claude.ai/code/artifact/250b31c9-8819-4a51-a1ba-3c7899054f41): every service, datastore, and the mechanism on every edge.
- **LLD** — [one toggle, start to finish](https://claude.ai/code/artifact/b516d49e-a704-4ba2-b273-014f9fac7cc3): the exact call sequence, and what's fire-and-forget.
- **DB design** — [three entities, one denormalization](https://claude.ai/code/artifact/4c71e139-99e6-4391-b316-1e3eae00ae41): the schema, and why the ledger's keys make uniqueness free.

## What you need going in

The same baseline as the rest of this project — client/server, HTTP — plus whatever module 01 of the URL shortener already covered (load balancers, caches, read replicas). This example spends its budget on the parts *that* example didn't need: an atomic counter, an async aggregation pipeline, and a queue used for decoupling rather than for user-facing async work.

## What "done" looks like

You should be able to answer, without looking back at module 01, *why this system never takes a database row lock on the hot path* — and be able to point to the exact line in the architecture (Redis `INCR`) and the exact line in the schema (`likes`' partition/clustering key) that make that true. Module 04's ten questions are the real test: if a question there surprises you, the module it traces back to is where to go re-read.
