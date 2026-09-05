# Module 02 — Low-Level Design

**Diagram for this module:** [Sequence diagram](https://claude.ai/code/artifact/a54c692c-dad5-4e4c-b957-98a36e8818d7)

## Which components get an LLD pass, and why only these

Of the three services in `01`, two have a real algorithmic/concurrency decision worth opening up: the **presence fan-out** (inside the Gateway/Presence Service pairing) and the **read-receipt state machine** (inside the Read Receipt Service). The Pub/Sub bus and Kafka topic are off-the-shelf infrastructure, not application logic — there's nothing to design there beyond configuration.

## Classes and interfaces

- **`ConnectionGateway`** *(per node)* — owns live sockets on that node. `subscribe(userId, channel)`, `push(userId, event)`. Knows nothing about how presence or receipts are computed, only how to move bytes to a socket it owns.
- **`PresenceStore`** *(interface)* → **`RedisPresenceStore`** — `refresh(userId)`, `isOnline(userId)`, `markOffline(userId)`.
- **`PresenceFanoutPublisher`** *(interface)* → **`PubSubPresencePublisher`** — `publish(userId, status)`.
- **`ReadReceiptService`** — `markRead(conversationId, userId, messageId)`; orchestrates the fast publish and the durable enqueue.
- **`ReceiptRepository`** *(interface)* → **`ShardedPostgresReceiptRepository`** — `advanceReadMarker(conversationId, userId, messageId, status)` (the conditional UPDATE from `01`).
- **`ReceiptEventQueue`** *(interface)* → **`KafkaReceiptQueue`** — `enqueue(event)`.

Same reasoning as the URL shortener's `UrlRepository` (see the root project's `02-lld-fundamentals.md`): `ReadReceiptService` depends on `ReceiptRepository` and `ReceiptEventQueue` as interfaces, not concrete classes, so swapping Postgres for a different durable store later — or swapping Kafka for another queue — never touches the service's own logic. `PresenceStore` and `PresenceFanoutPublisher` get the same treatment for the same reason.

## Pseudocode for the methods that matter

```
GatewayNode.onHeartbeat(userId):
    presenceStore.refresh(userId)          # SETEX user:{id}:online, 30s TTL
    # no fan-out here — fan-out only fires on a state TRANSITION, not every heartbeat

GatewayNode.onConnect(userId):
    wasOffline = not presenceStore.isOnline(userId)
    presenceStore.refresh(userId)
    if wasOffline:
        fanoutPublisher.publish(userId, "online")

GatewayNode.onDisconnect(userId, clean):
    if clean:
        presenceStore.markOffline(userId)
        fanoutPublisher.publish(userId, "offline")
    # if not clean: no action — the TTL expiry is the fallback path

ReadReceiptService.markRead(conversationId, userId, messageId):
    fanoutPublisher.publishFast(conversationId, {userId, messageId, status: READ})  # optimistic, no wait
    receiptQueue.enqueue({conversationId, userId, messageId, status: READ})          # durable, async, parallel

ReceiptConsumer.onEvent(event):
    repository.advanceReadMarker(event.conversationId, event.userId, event.messageId, event.status)
    # SQL: UPDATE conversation_receipts
    #      SET last_read_message_id = event.messageId, status = GREATEST(status, event.status)
    #      WHERE conversation_id = ? AND user_id = ? AND last_read_message_id < event.messageId
```

Two error cases worth designing for deliberately:

- **Duplicate/out-of-order receipt event.** Kafka's at-least-once delivery can redeliver an event the consumer already applied. The conditional UPDATE makes redelivery idempotent by construction — replaying an already-applied event matches zero rows and is a silent no-op, never a double-count or a regression.
- **Gateway crash mid-connection.** A client reconnecting after its gateway node dies must not assume the new node remembers anything about the old connection. The client sends an explicit `sync` message on reconnect carrying its last known state; the server only treats a fresh connection as authoritative for "online" after that sync completes, rather than guessing.

## Concurrency: where it's actually enforced, and why

`RedisPresenceStore.refresh` is a single atomic `SETEX` — safe to call concurrently from any of the ~2,500 gateway node instances, because each key is only ever written by connections belonging to that one user, and the union semantics from `01`'s "two devices, one key" race make concurrent same-user writes harmless. **No mutex, no distributed lock** — the atomicity of a single Redis command is the entire mechanism.

`ShardedPostgresReceiptRepository.advanceReadMarker` relies on the database's own row lock during the conditional `UPDATE` — **not** an application-level mutex. This only works because the check-and-write is one atomic SQL statement; a naive "read the row, compare in application code, then write" would need a distributed lock instead. This design deliberately keeps the read-check-write inside the database specifically to avoid needing that extra dependency.

Neither of these components runs a per-process **in-memory** mutex, because both run on many instances simultaneously (2,500 gateway nodes; a horizontally-scaled pool of Read Receipt Service workers) — an in-process mutex would only serialize writers on the *same* instance and do nothing for the other 2,499. Stating this explicitly matters: "just use a mutex" is a wrong answer the moment a component isn't pinned to exactly one instance.

## The sequence: `mark_read(conversationId, messageId)`

Open the [sequence diagram](https://claude.ai/code/artifact/a54c692c-dad5-4e4c-b957-98a36e8818d7). The important thing to notice: the fast path (top: Gateway A → Pub/Sub → Gateway B → Client B) and the durable path (bottom: Gateway A → Kafka → Receipt Service → Postgres) run **in parallel from the same originating call**, and neither waits on the other. The client sees "read" in ~200ms; the durable record lands a moment later; both are safe because the durable write is idempotent and monotonic, so it doesn't matter which one lands first from the database's point of view.

## Design patterns you just used, named

- **Repository pattern** — `ReceiptRepository` hides *how* read state is stored behind `advanceReadMarker`; the service never touches SQL directly.
- **Strategy pattern** — `PresenceStore` and `PresenceFanoutPublisher` are both swappable strategies behind stable interfaces (a Redis-backed store today, potentially something else later, with zero change to the services that call them).
- **Compare-and-set** — the conditional UPDATE is CAS at the database level, the same idea as an atomic `CAS(oldValue, newValue)` in shared-memory concurrency, just expressed as a `WHERE` clause instead of an instruction.

## Practice: extend it yourself

Before moving to module 03, sketch (even just in pseudocode) how you'd add:

1. **Typing indicators** ("Sarah is typing…"). Same shape as presence — ephemeral, TTL'd, pub/sub fanout — or does something about it actually differ? (Hint: how often does a typing event fire compared to a heartbeat, and does that change which layer should own rate-limiting it?)
2. **A 200-person group chat's read receipts.** Does `conversation_receipts` need a schema change, or does "read by 190/200" fall out of the existing per-(conversation, user) row shape as a query rather than a write? Where would the O(N) cost actually show up, if anywhere?

There's no single right answer to either — the point is noticing that the interface boundaries drawn above make it obvious *where* each extension belongs, the same lesson the URL shortener's own module 02 ends on.
