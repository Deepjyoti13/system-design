# Following One Request Across Twenty Microservices

Distributed tracing's operational reality — trace-ID propagation across sync AND async boundaries, and the sampling trade-off that keeps it affordable at scale — run through the same module shape as the rest of this project's deep dives.

## Why this example

This guide's [Logs, Metrics & Distributed Tracing](../content/scalability-resilience/logs-metrics-tracing.md) page already covers what a trace and a span ARE. This module covers what most conceptual explanations skip: propagating a trace ID across a queue boundary where there's no header to automatically carry it forward, and deciding what fraction of an enormous span volume is actually affordable to keep. That's a distinct interview shape from "explain observability" — it's "make this work across twenty independently-deployed services without any one of them being able to silently break the chain."

## How the modules fit together

| Module | Question it answers | File |
|---|---|---|
| 00 · Overview | What does "which of twenty services was slow" actually require, with no infrastructure in it yet? | [`00-overview.md`](./00-overview.md) |
| 01 · Architecture & HLD | What are the boxes, and why does the async/queue case need deliberate handling? | [`01-architecture-hld.md`](./01-architecture-hld.md) |
| 02 · LLD | What's the exact interface that makes propagation carrier-agnostic — HTTP header or queue message, same code path? | [`02-lld.md`](./02-lld.md) |
| 03 · DB Design | Why is a span store never a relational table? | [`03-db-design.md`](./03-db-design.md) |
| 04 · Interviewer Q&A | What would a real interviewer push on next? | [`04-interviewer-qna.md`](./04-interviewer-qna.md) |

Each of modules 01–03 ends with its own diagram.

## What you need going in

This guide's [Logs, Metrics & Distributed Tracing](../content/scalability-resilience/logs-metrics-tracing.md) (the concept this module operationalizes), [Message Queues & Pub/Sub](../content/hld-building-blocks/message-queues-pubsub.md) (the async boundary that makes propagation hard), and [Data Partitioning & Sharding](../content/hld-building-blocks/data-partitioning-sharding.md) (why `trace_id` is the natural shard key for span storage).

## What "done" looks like

You should be able to explain, without looking back at module 01, *why a trace can silently go incomplete at a queue boundary* — and name the exact fix (the publisher injects, the consumer extracts, same propagator interface as any synchronous call) as well as why the sampling decision has to be made exactly once, at the edge, rather than independently by each of the twenty services. Module 04's questions are the real test: the "why not trace 100%" and "debug a trace that disappears between two services" questions in particular are the ones this whole module exists to make you able to answer with a specific mechanism, not a vague instinct.
