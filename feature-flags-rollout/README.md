# Feature Flags: Shipping to 1% Before 100%

A worked example run through this project's usual lens (overview, architecture/HLD, LLD, DB design, interviewer Q&A) built around a different hard problem than most of this guide's other case studies: not "how do we make a read fast," but **how do we make a read cost nothing at all** — this system's evaluation path runs on the order of hundreds of millions of times a second, and it can never touch the network.

## Why this example

Almost every other design in this guide optimizes a read by putting a cache in front of a database. This one goes a step further: the read (`isEnabled(flag, user)`) never touches a cache, a database, or even a remote service — it's a local, in-process function call against an already-in-memory config snapshot. The interesting design problem isn't serving the read fast; it's getting a config change out to every instance in seconds without that distribution mechanism becoming its own bottleneck.

## How the modules fit together

| Module | Question it answers | File |
|---|---|---|
| 00 · Overview | What does the user (a PM flipping a switch) actually see? | [`00-overview.md`](./00-overview.md) |
| 01 · Architecture & HLD | Why does evaluation never leave the process, and how does a config change reach 10,000 instances? | [`01-architecture-hld.md`](./01-architecture-hld.md) |
| 02 · LLD | What's the evaluator's interface, and how does a snapshot swap without ever serving a half-updated read? | [`02-lld.md`](./02-lld.md) |
| 03 · DB Design | What does the control-plane schema look like, and why does it barely need to scale? | [`03-db-design.md`](./03-db-design.md) |
| 04 · Interviewer Q&A | What would a real interviewer push on next? | [`04-interviewer-qna.md`](./04-interviewer-qna.md) |

## What you need going in

The same baseline as the rest of this project — client/server, HTTP — plus a general sense of pub/sub and CDN caching (both covered in this guide's [HLD Building Blocks](../content/hld-building-blocks/)). This example spends its budget on an extreme read:write ratio and an immutable-snapshot swap, not on anything this guide's other case studies already cover in depth.

## What "done" looks like

You should be able to explain, without looking back at module 01, why this design's answer to "how do you scale the read path" is **"make the read never leave the process"** rather than "add a bigger cache" — and point to the exact mechanism (an atomically-swapped immutable snapshot, module 02) that makes an in-memory read safe under constant concurrent config updates. Module 04's questions are the real test: if one surprises you, the module it traces back to is where to go re-read.
