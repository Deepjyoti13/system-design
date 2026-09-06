# Module 02 — Low-Level Design

![Delivery-status state machine and the send/route/ack sequence](diagrams/lld.svg)

**`DeliveryStatus`**, as an explicit state machine, not a boolean (matching this guide's own convention elsewhere): `SENT → DELIVERED → READ`, with the monotonic-transition guard from above enforced at the data layer, not just trusted from the client.

## Interfaces vs. implementations

- **`MessageRepository`** *(interface)* → **`ShardedMessageStore`** — `append(conversationId, senderId, body, clientMsgId)`, `existsByClientMsgId(clientMsgId)`, `page(conversationId, cursor, limit)`. `GatewayConnection` and Message Service depend on this interface, never on the underlying sharded store directly.
- **`PresenceRouter`** *(interface)* → **`RedisPresenceRouter`** — `locate(user_id) -> gateway_instance_id | null`, `register(user_id, device_id, gateway_instance_id)`, `deregister(...)`. Message Service depends on the interface, not Redis directly, so the backing store could change without touching delivery logic.
- **`DeliveryQueue`** *(interface)* → **`RedisOfflineQueue`** — `enqueue(recipientId, messageId)`, `drain(recipientId) -> [messageId]`, called the moment `PresenceRouter.register` fires for a reconnecting recipient.
- **`GatewayConnection`** — the per-connection orchestrator. Depends on all three interfaces above, implements none of the storage or routing itself.

## Pseudocode for the core path

```
GatewayConnection.onMessage(client_msg_id, conversation_id, body):
    if MessageRepository.existsByClientMsgId(client_msg_id):     # idempotency check
        return existing_message_id                                # duplicate send, already handled
    message_id = MessageRepository.append(conversation_id, sender_id, body, client_msg_id)
    for recipient in Conversation.participants(conversation_id) - {sender_id}:
        gateway = PresenceRouter.locate(recipient)
        if gateway is not None:
            delivered = gateway.push(message_id)                  # online: deliver now
            if not delivered:
                DeliveryQueue.enqueue(recipient, message_id)       # push failed after all -- fall back
        else:
            DeliveryQueue.enqueue(recipient, message_id)           # offline: deliver on reconnect
    return message_id
```

## Error cases worth designing for deliberately

- **Duplicate send (idempotency-key collision):** `existsByClientMsgId` returning true is not an error — it's the expected outcome for a client retry after a flaky connection. Returning the existing `message_id` rather than re-appending is what makes `message.send` safe to call more than once.
- **`PresenceRouter.locate` says online, but the push itself fails** (the gateway instance crashed in the narrow window between the registry lookup and the actual push): the pseudocode above treats this as its own case, not a fatal error — a failed push falls back to `DeliveryQueue.enqueue`, the exact same path an offline recipient takes. The recipient's gateway registration is stale for at most one heartbeat interval before `PresenceRouter.deregister` catches up.

## Concurrency at the code level

`PresenceRouter.locate` and `PresenceRouter.register` need no in-process lock, and this is worth stating explicitly: `GatewayConnection` runs on thousands of Gateway instances simultaneously, so a language-level mutex would only ever protect one instance's own memory — it would do nothing about another instance registering the same user a moment later from a different device. Correctness comes entirely from the registry being a single shared, atomic store (a Redis `SET`/`GET`), the same pattern this guide applies everywhere two writers might race for the same logical entry: push the atomicity requirement down into the one system that can actually provide it.

The one place a genuine application-level decision matters: the `locate` → push sequence in `onMessage` is not itself atomic — the registry can say "online" and the actual push can still fail moments later (the case named above). This is exactly why the fallback path exists rather than trusting the registry lookup as a guarantee: `locate()` is a best-effort routing hint, not a promise, and the design treats it that way everywhere it's used.

## Design patterns you just used, named

- **Repository pattern** — `MessageRepository` and `DeliveryQueue` hide storage behind method calls; `GatewayConnection` never issues a query or a queue operation directly.
- **Strategy pattern** — fan-out itself is a strategy: fan-out-on-write (push to every participant at send time) and fan-out-on-read (recipients pull on reconnect, past the group-size threshold named in Module 01's Trade-offs) are two interchangeable strategies behind the same `onMessage` contract.
- **State pattern (via an explicit enum, not a class hierarchy)** — `DeliveryStatus`'s enforced one-way transitions are the same state-machine discipline this guide applies to [Payments](../payments-system/02-lld.md)'s `PaymentStatus`: model a lifecycle as named states with legal transitions, never as a boolean or a free-text field.

## Practice: extend it yourself

Before moving to Database Design, sketch (pseudocode is fine) how you'd add:

1. **Message editing and deletion** — a sender wants to edit a message five minutes after sending it. Does `MessageRepository` mutate the row in place, or does this need its own state (an `edited_at` timestamp, or a full edit-history table)? What happens to a copy of the message that's already sitting, undelivered, in a recipient's `DeliveryQueue` when the edit happens?
2. **Typing indicators** — `user_id is typing in conversation_id`, shown live to other participants. Does this belong on `PresenceRouter` (it's already a live, per-connection concept) or does it need an entirely separate, deliberately non-durable channel? What's different about this signal that makes it *not* need `MessageRepository` or `DeliveryQueue` at all?

Neither has one clean answer — the point is noticing that the interfaces already drawn make it obvious which existing component a new behavior is close to, and when a new behavior is different enough that it deserves its own path instead of being bolted onto one of these.
