# Module 00 — Feature Overview

![Rolling out new code to a live fleet without dropping a request](diagrams/01-architecture.svg)

## The feature, with no infrastructure in it yet

An engineer merges a change and ships it. Somewhere behind that button press, the new code has to end up running on every one of a fleet of servers that are, at this exact moment, answering live traffic — and not one request in flight anywhere in that fleet is allowed to see an error, a timeout, or a dropped connection *because of the deploy itself*. A user mid-checkout, a client holding an open WebSocket, a request that landed a millisecond before a server was told to stop — all of them have to come out the other side exactly as if no deploy had happened at all.

Two things about that description matter for everything that follows:

- **"Zero dropped requests" is the actual bar, not "very few."** A deploy that drops 0.01% of requests during a rollout isn't a mostly-solved version of this problem — it's a system that hasn't solved it, running at a scale where the failure is rare enough to miss in a demo.
- **The new code isn't the risk this module is about.** A bug in the new code is a different, orthogonal problem (canary analysis, rollback). This module is about the *mechanical* problem: even a perfect, bug-free new version can still drop requests if the rollout itself isn't done carefully — killing a process mid-request, or sending traffic to an instance before it's actually ready.

## What this module deliberately leaves out

No load balancer config, no health-check thresholds, no orchestrator state machine is named yet. If you can't state the mechanical problem — *code changes under a fleet that never stops serving* — in plain language first, it's easy to reach for "just use Kubernetes" without being able to say what Kubernetes is actually doing for you underneath. Module 01 opens the box.
