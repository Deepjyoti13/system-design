# Read Receipts & "Online Now" at Scale

A worked system-design deep dive on presence (online/last-seen) and per-message read receipts, carried through the same five-pass structure as this project's URL shortener: feature overview → architecture/HLD → LLD → DB design → interviewer Q&A.

## Why this example is a useful second case study

The URL shortener (see `../01-hld-fundamentals.md` etc.) is a request/response system: a client asks, the server answers. This feature is a **push** system: the server has to tell a connected client something changed, with nobody asking. That single difference forces a persistent-connection architecture (WebSocket + pub/sub fan-out) instead of a stateless request/response fleet — a genuinely different shape of problem, not a bigger version of the same one.

## How the modules fit together

| Module | Question it answers | File |
|---|---|---|
| 00 · Overview | What does the user actually see? | [`00-overview.md`](./00-overview.md) |
| 01 · Architecture & HLD | What are the services, and why a persistent-connection gateway plus two separately-scaled backing services? | [`01-architecture-hld.md`](./01-architecture-hld.md) |
| 02 · LLD | What are the classes inside the gateway and the receipt service, and where exactly is concurrency enforced? | [`02-lld.md`](./02-lld.md) |
| 03 · DB Design | What's actually persisted — and just as importantly, what *isn't*? | [`03-db-design.md`](./03-db-design.md) |
| 04 · Interviewer Q&A | The probing follow-ups this design should survive | [`04-interviewer-qna.md`](./04-interviewer-qna.md) |

Each of `00`–`03` ends with a diagram published as its own shareable page:

- **Overview** — [feature diagram](https://claude.ai/code/artifact/e5a81e89-488e-4194-a7ae-596baf4b461c): read-receipt progression and presence states, no infrastructure yet.
- **Architecture** — [HLD diagram](https://claude.ai/code/artifact/ccf0758e-704a-4f0e-a628-2569b3e38103): gateway fleet, pub/sub presence fanout, and the durable receipt pipeline, with every edge labeled by mechanism.
- **LLD** — [sequence diagram](https://claude.ai/code/artifact/a54c692c-dad5-4e4c-b957-98a36e8818d7): the exact call order for `mark_read`, fast path and durable path running in parallel.
- **DB design** — [schema diagram](https://claude.ai/code/artifact/aa61dc0a-5ff6-41ce-aa85-1121711473ba): the one table this feature owns, and why presence isn't a table at all.

## What you need going in

The same baseline as the root project (client/server, HTTP), plus a passing familiarity with WebSockets as "a connection that stays open" — that's defined the first time it matters, in module 00.

## What "done" looks like

You should be able to explain, out loud, why a *push* requirement (not a scale requirement) is what forces a persistent-connection architecture here — and trace how that one decision cascades into the pub/sub fan-out in `01`, the lock-free concurrency model in `02`, and the "no durable presence table" decision in `03`. If you can trace that chain, you've reproduced the same requirement → decision → interface → schema chain the URL shortener teaches, on a push-shaped problem instead of a request/response one.
