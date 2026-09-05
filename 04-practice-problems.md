# Module 04 — Practice Problems

The worked example handed you an HLD, an LLD, and a schema. This module doesn't — that's the point. For each problem below, work through the same three passes yourself before looking at the hints:

1. **HLD:** write down 3–4 functional requirements and 3–4 non-functional ones (scale, latency, read/write ratio) first, *then* draw the boxes. If you draw boxes before requirements, you'll design for guesses instead of constraints.
2. **LLD:** name the 4–6 classes involved, and for at least one of them, decide what's an interface vs. a concrete implementation, and why.
3. **DB design:** sketch the tables and name the one or two indexes that the busiest query actually needs.

A rough, hand-drawn version of all three beats a polished HLD with nothing underneath it — the goal is completing the chain, not perfecting any one link.

---

## 1. Rate Limiter

Design a service that limits each API client to N requests per time window, used in front of another service (it could sit in front of the URL shortener from modules 01–03).

**Constraints to consider:** does the limit need to be exact, or is "approximately N" acceptable? What happens under a distributed deployment — does every app server need to agree on the count, or can each one track its own slice?

<details><summary>Hints (try the problem first)</summary>

- HLD: this almost always needs a fast shared counter — a cache (Redis) sitting where every app server can reach it, not a per-server in-memory counter, or two servers each think they have the full quota.
- LLD: look up the *token bucket* and *sliding window* algorithms — this is one of the few problems where the algorithm choice belongs in the LLD write-up itself.
- DB design: this one barely needs a traditional database at all — the "storage" is almost entirely the cache. Notice when a problem doesn't need all three layers equally; that's a real finding, not a gap in your answer.
</details>

---

## 2. Parking Lot System

Design a system that tracks available spots across multiple levels and vehicle sizes (motorcycle, car, bus), and charges on exit based on duration.

**Constraints to consider:** how many spots realistically (hundreds, not billions) — this is a problem where the interesting design work is in the LLD and DB layers, not in scaling the HLD.

<details><summary>Hints (try the problem first)</summary>

- HLD: much smaller-scale than the URL shortener — no need for a cache or read replicas at this size. Resist the urge to add them anyway; matching complexity to actual scale is itself a skill being tested here.
- LLD: a natural fit for the *Strategy* pattern again — pricing rules and spot-assignment rules ("nearest available," "compact first") are both swappable strategies behind a stable interface.
- DB design: think about what "available" means when two entry points might try to assign the same spot at the same moment — where would you enforce that a spot can only be assigned to one vehicle at a time?
</details>

---

## 3. News Feed / Social Timeline

Design the system that shows a user a feed of recent posts from accounts they follow.

**Constraints to consider:** what happens when one account has 50 million followers? What happens when a user follows 3,000 accounts? These two extremes pull the design in opposite directions.

<details><summary>Hints (try the problem first)</summary>

- HLD: this is the classic *fan-out on write vs. fan-out on read* trade-off — look it up once you've drawn your first attempt, then reconsider it for the celebrity-account case specifically.
- LLD: a `FeedGenerationStrategy` interface with different implementations for "normal account" vs. "high-follower account" is a legitimate answer, not a cop-out.
- DB design: think about what's denormalized here the way `click_count` was denormalized in module 03 — precomputed feeds are exactly that trade-off at a larger scale.
</details>

---

## 4. One-to-One Chat / Messaging

Design a system where two users can exchange messages, with delivery status (sent / delivered / read) and message history.

**Constraints to consider:** messages need to arrive in order for a given conversation; a user might be offline when a message is sent.

<details><summary>Hints (try the problem first)</summary>

- HLD: this is the first problem in this set where a persistent connection (WebSocket) matters — think about what happens differently for an online recipient vs. an offline one, and how the offline case reuses concepts from module 01's async path.
- LLD: model delivery status as an explicit state machine (`sent → delivered → read`) rather than a boolean — it's the same instinct as module 02 distinguishing "not found" from "expired."
- DB design: think about how you'd choose a primary key that keeps a conversation's messages naturally ordered and easy to page through, without needing a global lock on write.
</details>

---

## When you're done with one

Compare your three-layer design against the worked example's structure — not the specific answer, since these are different systems, but the *shape*: did a non-functional requirement in your HLD show up again as an interface decision in your LLD, and again as an index or a denormalization in your DB design? If you can trace that chain for your own problem, you've learned the actual point of this project.
