# Message Queues & Pub/Sub

![Point-to-point queue (one consumer wins) vs. pub/sub (every subscriber gets a copy)](diagrams/message-queues-pubsub.svg)

## What problem this actually solves

A queue decouples a producer from a consumer in time and in failure — the producer doesn't need the consumer to be up, fast, or even to exist yet. That's the entire reason queues exist, not a vague "it's more scalable." The [URL Shortener](../case-studies/url-shortener/README.md) case study in this guide is the concrete version: click-analytics is queued specifically so a slow or down analytics worker can never block the redirect on the critical path. Remove the queue and the redirect's latency becomes hostage to the analytics worker's latency — the opposite of what the system is trying to guarantee.

## Point-to-point queue vs. pub/sub, precisely

These solve different problems, and picking the wrong one breaks things in a specific way, not just "suboptimally":

- **Point-to-point queue** (SQS-style) — each message is delivered to exactly one consumer among a competing pool. Good for work distribution: "do this job once." Use pub/sub here by mistake and every worker redundantly processes every job, unless you bolt on consumer-group semantics to fake queue behavior.
- **Pub/sub** (Kafka topics, SNS) — each message is delivered to *every* subscriber independently. Good for fan-out: many unrelated systems all need to react to the same event (an "order placed" event triggering email, inventory, and analytics as three separate consumers). Use a plain queue here by mistake and only one of those three systems ever sees any given event — the other two silently never fire.

## Delivery guarantees: pick the cheap one and make the consumer idempotent

- **At-most-once** — a message might just be dropped. Fine for data where an occasional loss is a non-event, like a metrics heartbeat.
- **At-least-once** — a message might be delivered more than once, but never silently dropped. This is what almost every real system actually runs.
- **Exactly-once** — extremely hard and expensive to guarantee end-to-end in a truly distributed system (it requires coordinating the producer, the broker, *and* the consumer's side effects atomically). What most systems that claim "exactly-once" actually built is **at-least-once delivery + an [idempotent consumer](idempotency-keys.md)** — processing the same message twice produces the same result as processing it once, which makes duplicate delivery harmless instead of trying to prevent it.

## Ordering and ack timing: two guarantees people assume they have

**Ordering** is per-partition, not global. A Kafka-style log preserves order only *within* one partition — there's no guarantee across partitions. If "this user's events must stay in order" matters, partition by `user_id` so every event for that user lands in the same partition; ordering across different users' events was never guaranteed and isn't something you're giving up.

**Ack timing** is a real trade-off, not a config detail:

- **Ack-on-receive** — the consumer acknowledges the moment it picks up the message, before processing it. If the consumer crashes mid-processing, that message is gone — the broker already considers it delivered.
- **Ack-on-process-complete** — the consumer acknowledges only after finishing. If the consumer crashes *after* finishing but *before* the ack reaches the broker, the broker redelivers it. That's a duplicate, not a loss — which is exactly why the idempotent-consumer pattern above isn't optional polish, it's what makes ack-on-process-complete safe to use at all.

## Interviewer follow-ups

**What happens to a message that keeps failing processing?**
Without a limit, a poison-pill message can loop forever, redelivered and re-failed on every ack timeout. Cap the retry count and route anything past it to a dead-letter queue — a place for a human or a separate process to inspect, instead of a message silently blocking or endlessly spinning the main queue.

**How would you handle a consumer that's falling behind a fast producer?**
That's backpressure, and the fix is on the consumer side, not the producer's: add more competing consumers (point-to-point queues parallelize this way for free), or let the queue's own depth grow as a buffer and alert on it, rather than having the producer block — blocking the producer usually just moves the slowdown upstream to whatever *it's* serving.

**Why might you choose Kafka over SQS for one system and the reverse for another?**
Kafka keeps a durable, replayable log and supports many independent consumer groups reading the same stream at different speeds — the right choice when multiple systems need their own pace against the same event history (analytics replaying last week's events, say). SQS is simpler operationally and fits point-to-point work distribution where nothing needs to replay history or fan out to independent readers.

**Does the producer need to know how many consumers exist?**
No, and that's the actual point of a queue — the producer publishes into the queue or topic and is done. Consumer count, consumer health, and consumer scaling are all decoupled from the producer entirely, which is the same decoupling this guide's [Client-Server Model](../foundations/client-server-model.md) page argues for at a smaller scale (stateless servers behind a load balancer).
