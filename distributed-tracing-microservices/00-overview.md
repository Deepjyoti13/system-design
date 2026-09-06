# Module 00 — Feature Overview

## The feature, with no infrastructure in it yet

A user reports "the app was slow for a second just now." Behind that one click, the request actually touched twenty different services — a gateway, an auth check, a dozen data-fetching calls, a couple of async side-effects. Somewhere in that chain, one hop took 900ms while the other nineteen took a few milliseconds each. The engineer's entire job is answering one question: **which one?**

Without anything built for this, the answer is "grep twenty different services' logs by approximate timestamp and hope nothing else was happening at the same moment" — which stops working the instant two users' requests overlap in time, which in a system with any real traffic is always.

Two things about that description matter enormously for everything that follows:

- **The request itself has to carry a thread through the whole chain.** Nothing about HTTP or a message queue naturally links "the auth check for user 4471's request" to "the same request's call to the recommendations service" — they're independent calls unless something explicitly ties them together.
- **The volume makes "trace everything, forever" impossible.** A system doing thousands of requests/sec, each touching twenty services, generates an enormous number of spans — keeping every one of them in full detail costs real money at scale, for detail that's almost never looked at.

That tension — needing a single thread through a system that has no natural single thread, at a volume that can't all be kept — is why this is its own module instead of a one-line "just add logging." Module 01 picks it up from there.

## What this module deliberately leaves out

No trace ID format, no storage engine, no sampling rate is named yet. This guide's own [Logs, Metrics & Distributed Tracing](../content/scalability-resilience/logs-metrics-tracing.md) page already covers the *concept* of a trace ID and a span; this module's job is the operational reality of doing it across twenty real services, at real volume, not repeating that page.
