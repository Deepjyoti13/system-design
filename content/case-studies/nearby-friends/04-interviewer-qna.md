# Module 04 — Interviewer Q&A

---

### 1. This is just the proximity service with moving points, isn't it?

The moving is the whole problem, and it inverts the design.

| | Proximity service (restaurants) | Nearby friends |
|---|---|---|
| Writes | ~never | **333,000/sec** |
| Reads per write | ~millions | **40** |
| Index | Build once, query forever | **Any index costs more to maintain than it saves** |
| Data lifetime | Permanent | **Expires in minutes** |
| Visibility | Public | Gated by a social graph |

A restaurant's location is written once and read a million times, so you index it heavily. A person's location is written every 30 seconds and read by ~40 people. **The write:read ratio is inverted**, so a geohash or quadtree over live positions would be rebuilt 333,000 times a second to serve 40 lookups — the index costs more than the scan it replaces.

So there is **no spatial index on the hot path at all.** Distance is computed pairwise, 40 times per update, between two known points. That looks like an omission until you see the numbers, and it's the single most important consequence of the points moving. [Geospatial Indexing](../../hld-building-blocks/geospatial-indexing.md) makes the same point generally: for rapidly-moving objects, O(1) update cost dominates query elegance.

---

### 2. How does one update reach 40 friends? Walk me through the fan-out.

**Publish once, to your own channel.** Not 40 sends.

```
WS server → SET loc:{me} EX 600
          → PUBLISH channel:{me}          ← ONE publish, regardless of friend count
Pub/sub   → delivers to every server subscribed to channel:{me}
Each such server → for each local client who is my friend:
                     haversine(their pos, my pos) <= their radius ? send : drop
```

The thing to notice is what the publisher **doesn't** do: no friend-list lookup, no "where is each friend connected", no fan-out loop. **The publisher doesn't know who's listening.**

Compare the design most people reach for first:

```
→ look up my 400 friends
→ look up which server each is connected to     ← A GLOBAL CONNECTION REGISTRY
→ send 40 targeted messages
```

That registry needs **333,000 lookups/sec, each returning up to 400 rows**, and it must be updated on every connect and disconnect across 10M clients. It's a global, hot, constantly-churning index. Publishing to your own channel eliminates it, because each server needs to know only *its own* clients' subscriptions — local state it already has.

[Module 02](./02-lld.md#interfaces-vs-implementations) encodes this in the interface: the method is `localConnectionsInterestedIn`, which *cannot* answer globally, so nobody can quietly reintroduce the lookup.

---

### 3. Why filter distance at the receiver? That wastes most of the pushes.

It does, and it's the right call for a specific reason — though I'd flag it as the design's clearest inefficiency.

Filtering at the *sender* requires the sender's server to know all 40 friends' current positions, which reintroduces exactly the global state the channel design just removed. Receiver-side filtering spreads 40 haversine computations across ~40 servers, each of which already holds the relevant client's last position locally.

But you're right about the waste: for a user whose friends are geographically scattered, the large majority of the 13.3M pushes/sec are delivered and *then* discarded.

**The fix I'd build next is geohash-prefixed channels**: publish to `channel:{user_id}:{geohash5}` and have subscribers subscribe only to prefixes near themselves. That filters before the push rather than after, cutting fan-out substantially.

And it has a benefit beyond efficiency that I'd actually lead with: **today a server receives a friend's exact position even when they're 500 miles away and should be invisible.** Nothing reaches the user, but the data crossed a process boundary it didn't need to. Geohash channels fix that structurally, which makes it a privacy improvement rather than just an optimization.

The complication to solve is that a user's subscription set must change as they *move* across cell boundaries, mid-session, without a visibility gap.

---

### 4. Where's the presence service? How do you know someone went offline?

There isn't one, and that's the design's cleanest simplification.

```
loc:{user_id} present in Redis  → active
loc:{user_id} expired (600s TTL) → invisible
```

**The TTL is doing three jobs simultaneously:** bounding cache memory (only active users occupy space, so ~1 GB regardless of 1 billion registered users), bounding staleness (nothing older than 10 minutes can be read as current), and implementing presence.

So "friends inactive for 10 minutes disappear" needs no heartbeats, no presence service, no online/offline events — and therefore no ordering between those events and no reconciliation when they're missed. An entire subsystem doesn't exist.

This is only available because [Module 00](./00-overview.md#what-dropped-points-are-acceptable-buys) accepted lossy semantics. A presence system with delivery guarantees would need explicit events, ordering, and repair. Reusing an eviction policy as a domain rule is only sound when the domain tolerates approximation — and here it does, because the data is stale by the time anyone reads it anyway.

---

### 5. You're dropping location updates. Isn't that a correctness problem?

No, and it's the requirement that unlocks the whole design, so I'd rather defend it than apologise for it.

Location data is **self-correcting**: the next update arrives in 30 seconds and overwrites whatever came before, regardless of what happened to the previous one. There is no accumulated state to lose. Contrast the [digital wallet](../digital-wallet/00-overview.md), where a dropped debit means money is permanently wrong — that's data with *history*, and history can't be reconstructed from the next message.

Because drops are tolerable:

- Fan-out is **fire-and-forget** — no acks, no retry queues, no delivery tracking. At 13.3M pushes/sec, per-message acknowledgement would be a bigger system than the one it protects.
- Live positions live in a **TTL cache** rather than a replicated database.
- A pub/sub rebalance or a server restart can drop in-flight updates with nothing to reconcile.
- Almost nothing in the code needs a lock, because the data is idempotent-by-overwrite.

[Module 02](./02-lld.md#interfaces-vs-implementations) puts this in the type system: `ChannelBus.publish` returns `void` with no callback, so a caller *can't* await confirmation and a future maintainer can't add retries without changing the interface and having that conversation deliberately.

**Recognising which data is self-correcting is the skill here.** Get it wrong in the safe direction and you build a wallet-grade pipeline for a weather forecast.

---

### 6. A user opens the app. What happens?

```
1. Authenticate; displace any older session for this user id
2. friendsOf(user_id)                 → ~400 friends, one DB read
3. SUBSCRIBE to friends' channels     → BATCHED BY OWNING NODE
4. MGET loc:{friend} for all friends  → ONE bulk read
5. Compute distances; send `init` with everyone currently nearby
```

Two details matter more than they look.

**Step 3 is batched by pub/sub node.** Naively, 400 friends means 400 `SUBSCRIBE` calls. Grouped by which node owns each channel, 400 friends across 133 nodes becomes **~133 requests** — and fewer in practice, since friends cluster geographically. That 3× reduction is what makes a mass reconnect merely slow instead of an outage.

**Step 4 exists to close a race.** A newly connected client has subscribed to friends' channels but has no history — so it would see nothing until each friend's next 30-second update, and an app that shows an empty screen for half a minute looks broken. The bulk read is the one place in the design that reads the location cache, and it's also what covers updates published in the window before the subscription completed.

The related detail from [Module 02](./02-lld.md#subscription-lifecycle): on **disconnect**, unsubscribe only from channels no other local client needs. Servers hold ~100,000 clients, so shared friends are common, and unsubscribing on the first client's disconnect would silently stop updates for the second. It fails quietly, which makes it the nastiest bug in the lifecycle.

---

### 7. What happens when a friend walks out of range?

They need an explicit `friend_left` message, and forgetting it is the classic bug in any radius-filtered push feed.

```
d = haversine(me, friend.pos)
if d > my_radius:
    if previously_in_range:
        send({type: "friend_left", user_id: friendId})    # ← easy to omit
    continue
```

Without it, the naive `continue` means the client never hears anything more about that friend — so they stay frozen on screen at their last in-range position, indefinitely, looking present. The bug is invisible in testing (everyone stays in range) and obvious in production.

The related implementation detail: **positions are cached even for out-of-range friends.** Two reasons — the departure check needs the previous position to know a *transition* occurred, and a client that stops receiving updates entirely can still render a friend at their last known place with an honest timestamp.

Which is also why the client's `ts` is echoed back rather than replaced with server time: the UI says "updated 4s ago", and that must reflect when the position was *measured*. Under fire-and-forget delivery those differ, and using server time would silently overclaim freshness.

---

### 8. A pub/sub node dies. What breaks?

Its channels rehash to other nodes via consistent hashing, affected WebSocket servers resubscribe, and **updates published during the move are lost.**

That's explicitly accepted — the next update arrives within 30 seconds. Roughly 1/133 of users have a few seconds of staleness. Nobody notices.

**Consistent hashing is what makes this survivable.** With plain modulo hashing, adding or removing one node would remap essentially all 100M channels, triggering a full resubscription storm across every WebSocket server. With a hash ring, ~1/n of channels move. Cross-ref [Consistent Hashing](../../hld-building-blocks/consistent-hashing.md).

The worse failure is a **WebSocket server** dying: its ~100,000 clients reconnect elsewhere and each re-subscribes to 400 channels. The connection loss is trivial; the **subscription storm** is the cost.

And the most visible failure is the **location cache** dying — live positions are gone, `init` returns empty, and users see nobody. The feature is effectively down. But it recovers within one refresh interval as updates repopulate it, with no repair action needed, because the data rebuilds itself. It's the loudest failure and the cheapest recovery in the system.

---

### 9. Your load is 333k writes/sec. How do you provision for growth?

The trap in that question is that **333k writes/sec is not the number that matters.** The workload is 13.3M *pushes*/sec, and the multiplier between them is a **social** parameter, not a technical one:

```
pushes/sec = updates/sec × (avg friends × fraction online)
13.3M      = 333k        × (400 × 10%)
```

So if average friend counts grow, or the online fraction rises from 10% to 25% at an evening peak, **push volume moves proportionally while write volume doesn't budge.** You can be perfectly provisioned for writes and 2.5× under-provisioned for pushes, from a change in user behaviour with no change in traffic volume. That's the metric to alert on.

The other thing worth noting: the pub/sub cluster needs **2 nodes' worth of memory and 133 nodes' worth of CPU** ([Module 00](./00-overview.md#capacity-estimation)). Sizing is entirely driven by push throughput; the 205 GB of channels is incidental. A capacity plan built on memory would be off by 60×.

And the most powerful lever available costs nothing to build: **raise the refresh interval from 30s to 60s.** That halves writes *and* pushes together, with a barely perceptible product change — because a person walking moves ~84 m in 60 seconds, still well inside a 5-mile radius. Making that interval a server-pushed config rather than a client constant is the single best piece of operational insurance in the design.

Second-cheapest: **suppress near-identical positions.** A stationary user — at a desk, asleep — publishes every 30 seconds and changes nobody's screen. That should be on permanently, not just under load.

---

### 10. What's the biggest weakness?

Three, and I'd lead with the operational one.

**The subscription storm on mass reconnection.** 10M clients × 400 friends is **4 billion subscriptions** if everyone reconnects at once — after a load-balancer failure, a bad deploy, or a carrier outage. Batching by owning node brings it to ~1.3 billion requests, and staggered reconnect with jitter plus sticky routing helps further, but the honest answer is that a full-fleet reconnect is a multi-minute recovery. It also means the friendship database has to be sized for a **failure mode** rather than for steady state, which is an uncomfortable place to leave a capacity decision. Letting clients cache their friend list and present it on reconnect — with the server validating rather than fetching — would be the real fix.

**Receiver-side filtering leaks position data across a process boundary.** Covered in answer 3: a server receives a friend's exact position even when they're far away and invisible. It's not a user-visible leak, and it's still more data movement than the privacy model implies. Geohash-prefixed channels fix it, and I'd prioritise them on privacy grounds rather than efficiency.

**1.15 TB/day of permanently-retained precise location history has no stated purpose beyond "analytics and ML".** That's a large privacy liability accumulated without a specific use justifying it, and there's no GDPR erasure path designed — a deletion request would have to reach Cassandra (across day-bucketed partitions, where deletes are tombstones that don't reclaim space until compaction), the Kafka backlog, the object-storage archive, and any warehouse copy. A defensible version would down-sample hard (one point per 5 minutes cuts it 10×) and set a retention limit derived from an actual use case rather than from "keep everything".

Also worth naming briefly: the **whale problem** (a 5,000-friend user generates 500 pushes per update, 12× normal) is capped by a product-level friend limit rather than solved, and there's no support for "nearby strangers" — that needs a genuinely different fan-out topology built on geohash channels rather than the friend graph.
