# Module 02 — Low-Level Design

**Diagram for this module:** [One Toggle, Start to Finish](https://claude.ai/code/artifact/b516d49e-a704-4ba2-b273-014f9fac7cc3)

## The two components worth opening up

Everything else in module 01 is "call a datastore." Two pieces have a real decision inside them worth designing at the class level: the **toggle orchestration** (what actually happens when a user taps like) and the **sharded counter** (how the count avoids ever taking a lock). Everything else — the durable Posts DB, the LB — is a standard building block with nothing new to say at this layer.

## Interfaces vs. implementations

- **`LikeLedger`** *(interface)* → **`NoSqlLikeLedger`** — `conditionalWrite(postId, userId, action)` returns whether the state actually changed; `exists(postId, userId)` is the point lookup for "did I like this."
- **`ShardedCounter`** *(interface)* → **`RedisShardedCounter`** — `increment(postId, delta)` picks a shard and issues an atomic `INCR`/`DECR`; `read(postId)` sums all shards.
- **`EventPublisher`** *(interface)* → **`KafkaEventPublisher`** — `publishAsync(event)`, never awaited by the caller.
- **`LikeToggleService`** — the orchestrator. Depends on all three interfaces, implements none of the storage itself — same repository-pattern discipline as the URL shortener's `UrlShortenerService`.

Naming the patterns: **`LikeLedger`/`ShardedCounter`** are the **Repository** pattern again (hide storage behind method calls). The shard-selection function inside `RedisShardedCounter` is a **Strategy** — `hash(caller) % N` today, swappable later for a dynamic hot-key-aware strategy without touching the service. The rate limiter sitting in front of the controller is a **Decorator**, exactly like the URL shortener's.

## Pseudocode for the method that matters

```
Service.toggle(userId, postId, action):        # action = LIKE or UNLIKE
    applied = ledger.conditionalWrite(postId, userId, action)
    if not applied:
        return { liked: ledger.exists(postId, userId) }   # idempotent no-op — see error case below

    delta = +1 if action == LIKE else -1
    counter.increment(postId, delta)             # atomic; RedisShardedCounter picks the shard internally

    publisher.publishAsync(LikeEvent(postId, userId, action, now()))   # fire-and-forget

    return { liked: action == LIKE }
```

Two error cases worth designing for deliberately:

- **Duplicate toggle (double-tap, retry):** `conditionalWrite` returning `applied=false` is not an error — it means this exact (user, post, action) already holds. The service returns the current state rather than re-incrementing the counter or re-publishing an event. Collapsing "already applied" into a generic exception here would be the same mistake the URL shortener's LLD warns against with "not found" vs. "expired": two different states, and the caller needs to know which one it got.
- **Counter increment fails after the ledger write already succeeded:** these are two different stores with no shared transaction, on purpose — a distributed transaction across a NoSQL ledger and Redis would reintroduce exactly the latency and contention this design exists to avoid. The ledger write is the one that must not fail silently (it's the correctness boundary); if the Redis increment errors, the toggle still returns success, and the event already queued for Kafka carries the delta for the aggregator to apply once the counter recovers. Name this lag explicitly rather than let it be a surprise: for a few seconds after a Redis blip, the fast count can under-count relative to the ledger's true state, self-healing once the aggregator's reconciliation catches up.

## Concurrency, at the code level

`RedisShardedCounter.increment` needs no in-process mutex, and this is worth stating explicitly rather than leaving as an implicit assumption: `LikeToggleService` runs on many horizontally-scaled instances, so an in-process lock would only ever protect against other threads *on the same instance* — it would do nothing about the other 200 instances also incrementing the same post's counter. Correctness here comes entirely from Redis's own atomicity guarantee on `INCR`, not from anything the Like Service does. This is the concrete answer to "how do you handle concurrent writers to the same counter": *the application does nothing — it delegates the one operation that needs atomicity to a system that provides it for free.*

The ledger's `conditionalWrite`, by contrast, does need a real conditional check — but it's enforced by the ledger store itself (a conditional put / `INSERT ... ON CONFLICT`), not by application-level locking either. Neither of this module's two components holds a lock anywhere; both push the atomicity requirement down into a storage layer built to provide it in O(1).

## Practice: extend it yourself

Before moving to module 03, sketch (pseudocode is fine) how you'd add:

1. **"Liked by A, B, and 41 others"** — which interface needs a new method, and does it belong on `LikeLedger` (it already knows every user who liked a post) or somewhere new?
2. **Undo-within-5-seconds** (tapping unlike right after like shouldn't count as two events for analytics) — does this belong in `LikeToggleService`, or is it something the aggregator should collapse using the flapping logic from module 01's concurrency table?

Neither has one right answer — the point is noticing that the interface boundaries already drawn make it obvious *where* the change would go.
