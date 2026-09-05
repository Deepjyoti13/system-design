# Ephemeral Content: How Stories Disappear After 24 Hours

A worked system-design example, built with the same three-layer method as the root project's URL shortener (overview → HLD → LLD → DB design), extended with a feature-overview pass up front and an interviewer question bank at the end. The running example: an Instagram/Snapchat-style "Story" — post a photo or video, your followers see it for 24 hours, then it's permanently gone.

## Why this example, and what it teaches that the URL shortener doesn't

The root project's URL shortener never deletes anything — every design decision is about serving reads fast against data that only grows. This example flips that: **disappearance is a functional requirement**, and doing that correctly at a billion-posts-a-day scale — without a batch job scanning billions of rows — is the one idea worth carrying away from it. Everything else (the cache, the queue, the sharding) will look familiar from the root project; the expiry mechanism is what's genuinely new.

## How the modules fit together

| Module | Question it answers | File |
|---|---|---|
| 00 · Overview | What does the user actually experience? | [`00-overview.md`](./00-overview.md) |
| 01 · Architecture & HLD | What are the services and datastores, and why does each exist? | [`01-architecture-hld.md`](./01-architecture-hld.md) |
| 02 · LLD | What are the classes inside Story Service, and what's the exact call sequence? | [`02-lld.md`](./02-lld.md) |
| 03 · DB Design | What does the schema actually look like, and why is it partitioned this way? | [`03-db-design.md`](./03-db-design.md) |
| 04 · Interviewer Q&A | What would a real interviewer push on, and how do the earlier decisions answer it? | [`04-interviewer-qna.md`](./04-interviewer-qna.md) |

Each of modules 00–03 ends with a diagram, published as its own page so you can open it full-size or share it:

- **Overview** — [feature diagram](https://claude.ai/code/artifact/f6d0cc2b-25cb-479b-9019-4141d77164b4): what the user sees, no infrastructure.
- **Architecture** — [full architecture diagram](https://claude.ai/code/artifact/7e6d2896-941f-4d93-8b50-43c5f24d1d07): every service, every datastore, every edge labeled with its mechanism.
- **LLD** — [sequence diagram](https://claude.ai/code/artifact/552c7fb9-9073-4af0-a0a9-395de08265f3): the exact call order for `getTray()`, including the Redis-down fallback branch.
- **DB design** — [ER diagram](https://claude.ai/code/artifact/e4dc7d0a-a9f6-46be-bcd1-6de15b7c503c): three tables, the partitioning key, and why.

## What you need going in

The root project's modules 01–03 (the URL shortener) — this one assumes you already know what HLD/LLD/DB design mean and moves straight to what's different here. No specific language or cloud provider assumed.

## What "done" looks like

You should be able to explain, out loud, why a Redis key with a 24-hour TTL is the *actual* mechanism that makes "stories disappear," why the SQL `expires_at` column is deliberately *not* the source of truth for that, and why the table is partitioned by day specifically because of that split. If you can trace that one chain, module 04's questions should feel answerable rather than like new material.
