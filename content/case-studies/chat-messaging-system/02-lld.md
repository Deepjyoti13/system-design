# Module 02 — Low-Level Design

![Delivery-status state machine and the send/route/ack sequence](diagrams/lld.svg)

**`DeliveryStatus`**, as an explicit state machine, not a boolean (matching this guide's own convention elsewhere): `SENT → DELIVERED → READ`, with the monotonic-transition guard from above enforced at the data layer, not just trusted from the client.

**`PresenceRouter`** *(interface)* → **`RedisPresenceRouter`** — `locate(user_id) -> gateway_instance_id | null`, `register(user_id, device_id, gateway_instance_id)`, `deregister(...)`. Message Service depends on the interface, not Redis directly, so the backing store could change without touching delivery logic.

Pseudocode for the core path:
```
GatewayConnection.onMessage(client_msg_id, conversation_id, body):
    if MessageStore.existsByClientMsgId(client_msg_id):     # idempotency check
        return existing_message_id                           # duplicate send, already handled
    message_id = MessageStore.append(conversation_id, sender_id, body, client_msg_id)
    for recipient in Conversation.participants(conversation_id) - {sender_id}:
        gateway = PresenceRouter.locate(recipient)
        if gateway is not None:
            gateway.push(message_id)                         # online: deliver now
        else:
            OfflineQueue.enqueue(recipient, message_id)       # offline: deliver on reconnect
    return message_id
```

Concurrency note: `PresenceRouter` and `MessageStore` are both shared, distributed state — this component runs on thousands of Gateway instances simultaneously, so any coordination here is a distributed-store operation (a Redis `SET`/lookup), never an in-process mutex; an in-process lock would only protect one instance's own memory; it's the wrong tool the moment there's more than one instance, which there always is at this scale.
