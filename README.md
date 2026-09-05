# System Design, Learn by Building

A small, self-contained project for learning system design by carrying **one example all the way through**: high-level design → low-level design → database design.

## Why one running example

Most system design material either stays abstract (principles with no worked example) or jumps between five different case studies, so you never see how a single decision at the top ripples down to a table schema at the bottom. This project uses **one example — a URL shortener** — and designs it three times, at three levels of zoom, so you can see that ripple directly.

A URL shortener is small enough to hold in your head, but it still forces every core decision a real system design conversation forces: read/write ratio and caching, an encoding scheme, interface boundaries, indexing, and the SQL-vs-NoSQL question. That's why it's the canonical teaching example in this space — this project just makes the connections between its layers explicit instead of leaving them implied.

## How the modules fit together

| Module | Question it answers | File |
|---|---|---|
| 01 · HLD | What are the boxes, and how does a request move through them? | [`01-hld-fundamentals.md`](./01-hld-fundamentals.md) |
| 02 · LLD | What are the classes and interfaces *inside* one of those boxes? | [`02-lld-fundamentals.md`](./02-lld-fundamentals.md) |
| 03 · DB Design | What does the data actually look like on disk? | [`03-db-design-fundamentals.md`](./03-db-design-fundamentals.md) |
| 04 · Practice | Can you do this again, on a different system, without the answer key? | [`04-practice-problems.md`](./04-practice-problems.md) |

Work through 01 → 02 → 03 in order the first time — each one hands off a concrete decision to the next (HLD decides there's a cache and a replica; LLD decides that lives behind a `CacheClient` interface; DB design decides what a cache miss actually queries). After that, 04 gives you four more problems to run through the same three lenses on your own.

Each of modules 01–03 ends with a diagram, published as its own page so you can open it full-size or share it:

- **HLD** — [architecture diagram](https://claude.ai/code/artifact/e3177f5c-0788-4be8-a830-f4de2a915547): write path, cached read path, async analytics.
- **LLD** — [class + sequence diagram](https://claude.ai/code/artifact/0ba9efda-fc3f-4db8-892f-7dd702559937): the interfaces behind the service, and the exact call order for a redirect.
- **DB design** — [schema / ER diagram](https://claude.ai/code/artifact/bc5f3561-f62e-43b0-8ce1-0e19cf1bed1a): three tables, two indexes, one denormalization.

## What you need going in

Just a basic sense of client/server and HTTP. Nothing here assumes a specific programming language, cloud provider, or prior system design experience — every term is defined the first time it's used.

## What "done" looks like

By the end of module 03 you should be able to look at the three diagrams together and explain, out loud, why each decision in a later diagram exists *because of* a decision in an earlier one. Module 04 is the real test: pick one of its problems and write your own three-level design — even a rough one — before checking it against the hints provided.
