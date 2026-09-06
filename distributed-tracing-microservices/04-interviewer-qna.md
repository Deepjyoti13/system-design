# Module 04 — Interviewer Follow-Up Bank

**What happens when two services emit spans for the same trace at nearly the same instant?**
There's no shared mutable state to race over — each span is an independent, append-only record keyed by `(trace_id, span_id)`, written once by the service that created it. "Concurrent" here just means two independent inserts, not a check-then-act race; the waterfall UI reconstructs order later from timestamps and `parent_span_id`, not from write order.

**What happens when traffic spikes 10x for an hour?**
Span volume spikes 10x too, proportionally — the Span Collector Agents keep batching and shipping asynchronously (no added latency on the traced requests themselves), but the Trace Storage tier needs headroom sized for peak, not average, span-write throughput. If it doesn't have that headroom, the fallback is dropping spans past a bounded local buffer at the collector — losing some trace detail during the spike is acceptable; blocking real request traffic on a saturated tracing backend is not.

**Why not just trace 100% of requests if storage is cheap?**
Because storage isn't actually the binding constraint at this volume — write throughput and query performance are. A store built to ingest and index a firehose of spans, then serve "give me everything for trace X" instantly, still has real per-write cost; at thousands of req/sec x 20 services, tracing everything forever means the tracing system's own infrastructure footprint rivals the production system it's observing, for data that's read maybe 0.1% of the time it's written. Sampling is a deliberate cost/coverage trade, not a workaround for a technical limit that could just be engineered away.

**Why does the sampling decision have to be made once, at the edge, instead of letting each service decide independently?**
Because independent per-service decisions produce a trace with holes in it. If service 12 of 20 independently rolls "don't sample" while services 1–11 and 13–20 rolled "sample," the resulting trace is missing exactly the span that might have been the slow one — worse than useless, since it looks complete but isn't. Propagating one decision as part of the trace context (module 01, module 02's `sampled` field carried on every hop) is what guarantees a sampled trace is always the FULL trace, never a partial one.

**How would you debug a trace that "disappears" between two specific services?**
Check whether that hop crosses an async boundary (a queue publish/consume) — module 01 names this as the actual hard case. The most common root cause is a publisher that never injected the trace context into the message, or a consumer that never extracted it before continuing — both are propagation bugs, not storage or sampling bugs, and they're specific to that one integration point, not the tracing system as a whole.

**Could tail-based sampling replace head-based sampling entirely, since it's strictly more accurate?**
Only at a real infrastructure cost: tail-based sampling means buffering every span from every service until each trace finishes and a keep/drop decision can be made — a genuinely heavier collector architecture than head-based sampling's "flip a coin once at the edge, ship immediately." Most real systems run head-based as the default (cheap, always-on) with a tail-based override layered on top specifically for errors and high latency, rather than replacing head-based sampling outright.

**How is this different from just centralizing logs from all twenty services?**
Centralized logs answer "what happened in each service," searchable by timestamp and text. A trace answers a different question: "which of these twenty services' work belongs to THIS one request, and in what nested order." Without a propagated trace ID connecting them, centralized logs from twenty services during a traffic spike are twenty interleaved streams with no way to tell which lines belong together — cross-ref [Logs, Metrics & Distributed Tracing](../content/scalability-resilience/logs-metrics-tracing.md)'s framing of traces as answering a question logs and metrics can't answer alone.

**How would you test that context propagation actually survives the async/queue boundary, before finding out it's broken in production?**
Same answer this guide gives for any "hope it works" failure path — deliberately trigger it. Publish a message through the real queue in a staging environment and assert the consumer's extracted `trace_id` matches the publisher's, as an automated check in the pipeline that touches that queue integration, rather than trusting that every future publisher/consumer pair remembers to wire propagation in correctly.
