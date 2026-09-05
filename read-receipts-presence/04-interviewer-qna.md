# Module 04 — Interviewer Follow-Up Bank

Probing questions an interviewer would actually ask about this specific design, each answered from a decision already made in `01`–`03` — not a generic textbook answer.

---

**1. What happens when two requests hit the same resource at the same instant** — e.g. a user's phone and web client both send `mark_read` for overlapping message ranges within milliseconds of each other?

The conditional `UPDATE ... WHERE last_read_message_id < :new_id` (module 01/02) makes this safe without any application-level locking. Postgres's row lock during that single atomic statement serializes the two writers; the one with the lower message ID simply matches zero rows and no-ops. Neither request errors, and the final state is always "read up to the highest message ID either device actually saw" — correct by construction, not by retry logic.

**2. What happens when traffic spikes 10x for an hour** — say, a major news event drives a burst of simultaneous app opens?

Module 01's load-handling section names the exact order of degradation: heartbeat interval stretches from 15s to 30s first (halves heartbeat load, slower offline detection, invisible to most users); the load balancer stops routing *new* connections to gateway nodes near their cap (existing connections are unaffected); only as a last resort does the Kafka receipt topic apply backpressure, which delays durability, never message delivery. The 20%-headroom fleet exists specifically to absorb the first few minutes before autoscaling — which can't help a *stateful* gateway fleet instantly — catches up.

**3. How do you detect a client that disconnected silently** (phone loses signal, no clean TCP close)?

There's no explicit detection — the Redis presence key's 30s TTL is the fallback. If heartbeats stop, the key simply expires and the user reads as offline within 30s. This is a named, bounded staleness window, not a gap in the design.

**4. Why not just poll for presence every few seconds instead of holding open a WebSocket?**

At 150M concurrent users, repeated poll requests mean a constant rate of new-connection setup (and often a TLS handshake) that dwarfs the cost of one heartbeat over an already-open connection. The trade-offs table in module 01 makes this explicit — this is the transport decision the whole architecture pivots on.

**5. How would you support read receipts for a 200-person group chat without an O(N) write per message?**

The schema doesn't change — it's still one row per (conversation, user). A group chat's "read by 190/200" view is a **read-side aggregation** (`SELECT COUNT(*) ... WHERE status = READ`) over rows that already exist for other reasons, not an extra write fan-out. The cost shows up in reads, not writes, and only for the (rare) UI that actually displays a per-message read count.

**6. What if the Kafka receipt topic falls behind by several minutes** — a broker issue, a slow consumer?

Nothing on the message-sending or real-time-UI path is affected — that's the entire point of decoupling the fast pub/sub push from the durable Kafka write in module 01. The only visible symptom is a device that was offline during the lag and reconnects mid-delay: it reads slightly stale receipt state from Postgres until the consumer catches up.

**7. How do you avoid a thundering herd of reconnects after a regional outage or app-wide restart?**

Not fully solved by the backend alone — client-side reconnect logic needs jittered exponential backoff so 150M clients don't all retry in the same second. On the server side, the load balancer's admission control (module 01's shedding order) and the 20%-headroom fleet absorb the reconnect spike the same way they absorb any other load spike; nothing about reconnect storms is architecturally special beyond "it's a load spike with a very synchronized start time."

**8. Why is presence not stored durably anywhere?**

Module 03's schema section makes this explicit: nothing in the functional requirements ever asks for presence *history* — only "is this person online right now." A durable table for a fact nobody queries historically would be unrequested complexity; a Redis key with a TTL gives correct, self-healing behavior (a crashed node loses nothing that a 30-second staleness tolerance didn't already permit) with zero cleanup code.

**9. Argue the other way: why not make the read-receipt write synchronous before pushing the real-time event, to guarantee the visible state and the durable state can never diverge, even briefly?**

Because the divergence window is bounded (hundreds of milliseconds) and self-heals via the monotonic write — the visible checkmark and the durable row always converge to the same answer regardless of arrival order. Making the write synchronous would reintroduce, on every single message read, exactly the tail latency the persistent-connection architecture was built to avoid — trading a real, felt latency cost for a guarantee (zero-width consistency window) that nothing in the requirements actually asks for.

**10. How would a GDPR "right to be forgotten" request interact with this design?**

Deleting a user's receipt rows is a straightforward point-delete filtered by `user_id`, scoped by the shard key (`conversation_id`) the application already knows for each of that user's conversations — no cross-shard fan-out or cascading delete needed, because `conversation_receipts` isn't replicated or denormalized anywhere else in this design.
